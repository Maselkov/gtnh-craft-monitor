// Power tab: readings, trend and chart.

import { CHART_RANGE_SECONDS_JS, CHART_TIME_CONFIG, chartXTicks } from './history.js';
import { machineLabel, powerBadge, setTabBadge } from './tab-badges.js';
import {
  chartColors, delegateActions, escapeHtml, formatDuration, formatRate, onBackdropClick, onThemeChange, pollFetch,
} from './util.js';

// ---------- Power chart ----------
let powerRange = 'day';
// The machine picked with the buttons above the chart (or ?machine= in
// the URL); null shows the server's default, the first one.
let powerMachine = null;
let powerChart = null;
let lastPowerData = null;  // separate from crafts' lastData on purpose -
                            // power comes from a different Lua script
                            // (power_monitor.lua, reading gt_machine),
                            // polled on its own 15s cadence, so its
                            // freshness has nothing to do with crafts'
                            // me_controller-based "updated Xs ago" line

function setPowerRange(range) {
  powerRange = range;
  document.querySelectorAll('#powerRanges .range-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.range === range);
  });
  fetchPower();
}

// Unlike tickSourceLine (which extrapolates from a cached age_seconds
// using client-side elapsed time), this recomputes directly from
// latest.t - an absolute server timestamp - every tick, so there's
// nothing to extrapolate or keep in sync separately.
export function tickPowerSourceLine() {
  const el = document.getElementById('powerSourceLine');
  if (!lastPowerData || !lastPowerData.latest) {
    el.textContent = 'Waiting for data from power_monitor.lua...';
    return;
  }
  const age = Math.max(0, Math.round(Date.now() / 1000 - lastPowerData.latest.t));
  el.textContent = `Updated ${age}s ago`;
}

export function formatEU(n) {
  if (n == null) return '?';
  const abs = Math.abs(n);
  // The wireless network's EU runs far past T.
  if (abs >= 1e21) return n.toExponential(2);
  if (abs >= 1e18) return (n / 1e18).toFixed(2) + 'E';
  if (abs >= 1e15) return (n / 1e15).toFixed(2) + 'P';
  if (abs >= 1e12) return (n / 1e12).toFixed(2) + 'T';
  if (abs >= 1e9) return (n / 1e9).toFixed(2) + 'G';
  if (abs >= 1e6) return (n / 1e6).toFixed(2) + 'M';
  if (abs >= 1e3) return (n / 1e3).toFixed(2) + 'k';
  return String(Math.round(n));
}

// "64.9P": three digits and no unit, short enough for the tab button.
export function compactEU(n) {
  if (n == null) return '';
  const units = ['', 'k', 'M', 'G', 'T', 'P', 'E'];
  let i = 0;
  while (i < units.length - 1 && Math.abs(n) >= 1000 ** (i + 1)) i++;
  let digits = (n / 1000 ** i).toPrecision(3);
  // 999.96T rounds to "1.00e+3": that's 1.00P.
  if (Math.abs(Number(digits)) >= 1000 && i < units.length - 1) digits = (n / 1000 ** ++i).toPrecision(3);
  if (Math.abs(Number(digits)) >= 1000) return n.toExponential(1);  // past E
  return i === 0 ? String(Math.round(n)) : digits + units[i];
}

// The Power tab's URL: /power, or /power?machine=... once one is picked.
// The unnamed machine is ?machine=Main, as its button reads (the server
// takes that for it), unless a machine is really called "Main".
export function powerPath() {
  if (powerMachine == null) return '/power';
  const named = (lastPowerData?.machines || []).some(m => m.name === 'Main');
  const shown = powerMachine === '' && !named ? 'Main' : powerMachine;
  return `/power?machine=${encodeURIComponent(shown)}`;
}

// After loading on, or navigating back to, a /power URL.
export function powerMachineFromUrl() {
  powerMachine = new URLSearchParams(location.search).get('machine');
}

function setPowerMachine(name) {
  // '' is the unnamed machine, picked like any other (?machine=), not
  // the default: that's the wireless network while it reports.
  powerMachine = name ?? null;
  history.replaceState({ view: 'power' }, '', powerPath());
  fetchPower();
}

export { machineLabel };

// A machine that hasn't reported for this long (health.py's
// POWER_STALE_SECONDS) is left out of the tab's fill, so one taken down
// doesn't hold the total at its last reading forever.
const FILL_STALE_SECONDS = 300;

// What a machine's fill is measured against: an LSC in wireless mode
// keeps only its wireless threshold (it trades the rest with the
// network every 5 minutes), so that, not its capacity, is its "full".
export function fillSize(m) {
  return m.wireless_threshold > 0 ? m.wireless_threshold : m.capacity;
}

// One machine's fill, which goes past 1 for an LSC holding more than its
// wireless threshold until the next upload. null without a size.
export function machineFill(m) {
  const size = fillSize(m);
  return size > 0 && m.stored != null ? Math.max(0, m.stored / size) : null;
}

// How full every machine is together, 0..1: total stored over total
// size, so a small buffer counts for its size, not as much as the
// LSC. null when nothing has a size.
export function powerFill(machines, now = Date.now() / 1000) {
  const known = (machines || []).filter(m => fillSize(m) > 0 && m.stored != null);
  const fresh = known.filter(m => now - m.t <= FILL_STALE_SECONDS);
  const counted = fresh.length ? fresh : known;
  const size = counted.reduce((sum, m) => sum + fillSize(m), 0);
  if (!size) return null;
  return Math.min(1, Math.max(0, counted.reduce((sum, m) => sum + m.stored, 0) / size));
}

// "61%", or "<1%" rather than a misleading 0% while there's some left.
export function fillText(fraction) {
  if (fraction == null) return '';
  if (fraction > 0 && fraction < 0.005) return '<1%';
  return `${Math.round(fraction * 100)}%`;
}

// What the Power tab button shows: the wireless network's EU while it
// reports (an LSC in wireless mode moves all but its threshold there,
// so its own fill stays under 1%), else how full the machines are.
export function powerTabText(machines, now = Date.now() / 1000) {
  const wireless = (machines || []).find(m =>
    !(m.capacity > 0) && m.stored != null && now - m.t <= FILL_STALE_SECONDS);
  return wireless ? compactEU(wireless.stored) : fillText(powerFill(machines, now));
}

// Its span is always there, empty without data, so the tabs after it
// keep their place.
function setPowerTabFill(machines) {
  document.getElementById('tabFillPower').textContent = powerTabText(machines);
}

// The machine buttons' order: the wireless network (no capacity) first,
// as it's where the base's power is.
export function orderMachines(machines) {
  const all = machines || [];
  // Then by size, largest first (an LSC in wireless mode by its threshold),
  // by name between equals. A machine's number never changes with it.
  const bySize = (a, b) => (fillSize(b) || 0) - (fillSize(a) || 0)
    || machineLabel(a.name).localeCompare(machineLabel(b.name));
  return [...all.filter(m => !(m.capacity > 0)), ...all.filter(m => m.capacity > 0).sort(bySize)];
}

// What a machine button shows after its name: its fill, or for the
// wireless network, which has no "full", its EU.
export function machineButtonText(m) {
  if (!(m.capacity > 0)) return m.stored != null ? compactEU(m.stored) : '';
  return fillText(machineFill(m));
}

// The machine the tab's red dot is about (powerBadge's machine), whose
// button is tinted red; null with no dot.
let alertMachine = null;

// The tab badge, from the default machine's data (/api/power without
// ?machine=), and which machine it's about.
function setPowerBadge(data) {
  const badge = powerBadge(data.latest && { ...data.latest, name: data.machine }, data.trend, data.machines);
  alertMachine = badge ? badge.machine : null;
  setTabBadge('power', badge);
}

function renderMachines(data) {
  const box = document.getElementById('powerMachines');
  const machines = orderMachines(data.machines);
  box.hidden = machines.length < 2;
  box.innerHTML = machines.map(m => {
    const fill = machineButtonText(m);
    const classes = ['range-btn'];
    if (m.name === data.machine) classes.push('active');
    if (m.name === alertMachine) classes.push('alert');
    return `<button class="${classes.join(' ')}"
    data-action="power-machine" data-machine="${escapeHtml(m.name)}"${
      m.name === alertMachine ? ' title="What the Power tab\'s red dot is about"' : ''}>${escapeHtml(machineLabel(m.name))}${
      fill ? ` <span class="machine-fill">${escapeHtml(fill)}</span>` : ''}</button>`;
  }).join('');
}

// Each fetchPower() is numbered, and only the latest one's answer is
// shown: a click on a machine or range while an earlier fetch (another
// click, the 15s refresh) is still out would otherwise be overwritten
// when that earlier answer arrives last - showing, and letting an admin
// rename, the machine before.
let powerFetchSeq = 0;

export async function fetchPower() {
  const seq = ++powerFetchSeq;
  try {
    const query = new URLSearchParams({ range: powerRange });
    if (powerMachine != null) query.set('machine', powerMachine);
    const res = await pollFetch('/api/power?' + query);
    const data = await res.json();
    if (seq !== powerFetchSeq) return;
    lastPowerData = data;
    // ?machine=Main found the unnamed machine: that's the one picked.
    if (powerMachine === 'Main' && data.machine === '') powerMachine = '';
    renderPower(data);
    tickPowerSourceLine();
  } catch (e) {
    if (seq !== powerFetchSeq) return;
    document.getElementById('powerSourceLine').textContent = 'Could not reach server.';
  }
}

// Custom tooltip, not the native title="..." one - a plain title
// attribute can't hold colored text, which this needs for the in/out
// breakdown. CSS-driven show/hide (.power-trend-wrap:hover) - purely
// informational, nothing inside needs to be reached or clicked, so
// the gap-crossing hover-loss that broke the earlier window-toggle-
// buttons version (moving the cursor down to reach them crossed a
// dead zone outside the wrapper's own layout box, dropping :hover
// before the cursor arrived - a real, known CSS tooltip limitation,
// not something worth fixing here for buttons that added a lot of
// interaction risk for not that much value) doesn't matter for this.
//
// Fixed to the 5s window - 5m/1h averages are still sent and stored
// (server-side, in case they're wanted for something else later),
// just not surfaced here anymore.
function buildPowerTrendHtml(latest) {
  if (latest.avg_eu_in_5s == null || latest.avg_eu_out_5s == null) return '';
  // The LSC averages EU/t (its "time to full" only works out that way):
  // 20 ticks a second.
  const avgIn = latest.avg_eu_in_5s * 20;
  const avgOut = latest.avg_eu_out_5s * 20;

  const net = avgIn - avgOut;
  let arrowHtml;
  if (net > 0) {
    arrowHtml = `<span class="power-trend power-trend-up">&#9650; ${formatEU(net)} EU/s</span>`;
  } else if (net < 0) {
    arrowHtml = `<span class="power-trend power-trend-down">&#9660; ${formatEU(Math.abs(net))} EU/s</span>`;
  } else {
    arrowHtml = `<span class="power-trend">&#9679; steady</span>`;
  }

  return `
    <span class="power-trend-wrap">
      ${arrowHtml}
      <div class="power-trend-tooltip">
        <div class="power-trend-tooltip-row">
          <span class="power-trend-in">In: ${formatEU(avgIn)} EU/s</span>
          <span class="power-trend-out">Out: ${formatEU(avgOut)} EU/s</span>
        </div>
      </div>
    </span>
  `;
}

// The line under an LSC in wireless mode's readout, in place of "Full
// in"/"Empty in" (the network refills it every 5 minutes): what it sent
// to or drew from the network over the past hour (the server's
// wireless_trade, EU/s: not its own in/out averages, which count the
// transfers too), and when drawing, the most its threshold can refill -
// one threshold's worth per 5-minute rebalance.
// As parts, [{text, tone}]: the amount is 'up' (green) when it sends,
// 'down' (red) when it draws, as the arrow beside it.
export function wirelessLscParts(latest, net) {
  const refill = `${formatEU(latest.wireless_threshold / 300)} EU/s`;
  if (net == null) return [{ text: `Refills up to ${refill}` }];
  if (net >= 0) {
    return [{ text: 'Sending ' }, { text: `${formatEU(net)} EU/s`, tone: 'up' },
      { text: ' to the wireless network over the past hour' }];
  }
  return [{ text: 'Drawing ' }, { text: `${formatEU(-net)} EU/s`, tone: 'down' },
    { text: ` from the wireless network over the past hour \u00b7 refills up to ${refill}` }];
}

export function wirelessLscText(latest, net) {
  return partsText(wirelessLscParts(latest, net));
}

function partsText(parts) {
  return parts.map(p => p.text).join('');
}

// "Full in 3h 5m · +1.20G EU/h over the past hour", from the server's
// fit to the last hour of readings (gcm/rates.py) - steadier than the
// 5s in/out arrow, which follows every machine switching on and off.
// As parts, [{text, tone}]: only the rate is coloured, by its sign. The
// fit is over an hour, steady enough for a colour to mean something;
// "Empty in" stays plain, the tab's red dot being the warning.
export function powerEtaParts(trend) {
  if (!trend) return [];
  const rate = formatRate(trend.per_second, ' EU');
  if (!rate) return [];
  const lead = [];
  if (trend.seconds_to_full != null) lead.push(`Full in ${formatDuration(trend.seconds_to_full)}`);
  if (trend.seconds_to_empty != null) lead.push(`Empty in ${formatDuration(trend.seconds_to_empty)}`);
  const tone = trend.per_second > 0 ? 'up' : trend.per_second < 0 ? 'down' : undefined;
  return [
    ...(lead.length ? [{ text: lead.join(' \u00b7 ') + ' \u00b7 ' }] : []),
    { text: rate, tone },
    { text: ' over the past hour' },
  ];
}

export function powerEtaText(trend) {
  return partsText(powerEtaParts(trend));
}

// Off the Power tab too, for its tab badge: the last hour is enough
// for the trend, and far fewer points than the chart's range.
export async function fetchPowerBadge() {
  try {
    const res = await pollFetch('/api/power?range=hour');
    const data = await res.json();
    setPowerBadge(data);
    setPowerTabFill(data.machines);
  } catch (e) { /* keep the last badge */ }
}

function renderPower(data) {
  const points = data.points || [];
  // Before the buttons, which tint the machine the badge is about.
  if (powerMachine == null) setPowerBadge(data);
  renderMachines(data);
  // Admins only (admin-only), and only with a machine to rename.
  document.getElementById('powerRenameBtn').hidden = !data.latest;
  setPowerTabFill(data.machines);
  const empty = document.getElementById('powerEmpty');
  const chartWrap = document.getElementById('powerChartWrap');
  const canvas = document.getElementById('powerChart');
  document.getElementById('powerConnectNote').hidden = Boolean(data.latest);

  if (data.latest) {
    const { stored, capacity, wireless_threshold: threshold } = data.latest;
    // No capacity: the wireless network, which holds any amount. An LSC
    // in wireless mode: its threshold, the 5-minute buffer it keeps.
    let ofCapacity = '';
    if (threshold > 0) {
      ofCapacity = ` / ${formatEU(threshold)} EU wireless threshold (${(stored / threshold * 100).toFixed(1)}%)`;
    } else if (capacity > 0) {
      ofCapacity = ` / ${formatEU(capacity)} EU (${(stored / capacity * 100).toFixed(1)}%)`;
    }
    const trendHtml = ' ' + buildPowerTrendHtml(data.latest);
    document.getElementById('powerCurrent').innerHTML =
      `<span class="big">${formatEU(stored)} EU</span>${ofCapacity}${trendHtml}`;
    const eta = document.createElement('div');
    eta.className = 'power-eta';
    eta.id = 'powerEta';
    // An LSC in wireless mode is refilled every 5 minutes: an hour's
    // drift between refills says nothing about when it runs out.
    // wirelessLscText says what it trades with the network instead.
    const parts = threshold > 0 ? wirelessLscParts(data.latest, data.wireless_trade) : powerEtaParts(data.trend);
    eta.append(...parts.map(part => {
      if (!part.tone) return document.createTextNode(part.text);
      const span = document.createElement('span');
      span.className = part.tone === 'up' ? 'power-trend-up' : 'power-trend-down';
      span.textContent = part.text;
      return span;
    }));
    // Always there, a line high even when empty, so switching machines
    // doesn't move the chart up and down.
    document.getElementById('powerCurrent').append(eta);
  } else {
    document.getElementById('powerCurrent').textContent = 'No readings yet';
  }

  if (points.length === 0) {
    empty.style.display = data.latest ? 'block' : 'none';
    chartWrap.style.display = 'none';
    return;
  }
  empty.style.display = 'none';
  chartWrap.style.display = '';

  // {x, y} point objects, not a separate labels array - matches the
  // item-history chart's own time-scale approach (see that chart's
  // comment for the full reasoning): each point carries its own
  // real position rather than relying on index-alignment with a
  // parallel label list, which is what caused uneven, misleading
  // tick spacing on a category axis.
  const storedData = points.map(p => ({ x: p.t * 1000, y: p.stored }));
  // The second line is the capacity, or for an LSC in wireless mode its
  // threshold: against a capacity of 9.22E its few P would lie flat on 0.
  // Readings from before it was in wireless mode have none, and leave a gap.
  const byThreshold = points.some(p => p.wireless_threshold > 0);
  const sizeLabel = byThreshold ? 'Wireless threshold' : 'Capacity';
  const capacityData = points.map(p => ({
    x: p.t * 1000, y: byThreshold ? (p.wireless_threshold > 0 ? p.wireless_threshold : null) : p.capacity,
  }));
  const noCapacity = !byThreshold && points.every(p => !(p.capacity > 0));
  // An LSC in wireless mode spends its time just above its threshold
  // (6.00P to 6.37P, say), so the axis would start right on the threshold
  // and hide its line in the bottom edge: leave some room around the data.
  // Not for a capacity, where room below would mean negative EU.
  const yGrace = byThreshold ? '10%' : 0;

  const nowMs = Date.now();
  const rangeSeconds = CHART_RANGE_SECONDS_JS[data.range];
  // Bounded ranges get a fixed axis window (now - range to now), so
  // "day" always genuinely shows a full day - same reasoning as the
  // item-history chart. Power sampling is dense and regular (every
  // ~60s) rather than change-only, so this matters less here than
  // it did for item history, but it's the same correct behavior
  // either way and keeps both charts consistent.
  const axisMin = rangeSeconds ? nowMs - rangeSeconds * 1000 : undefined;
  const axisMax = nowMs;

  if (powerChart) {
    powerChart.data.datasets[0].data = storedData;
    powerChart.data.datasets[1].data = capacityData;
    powerChart.data.datasets[1].label = sizeLabel;
    powerChart.data.datasets[1].hidden = noCapacity;
    powerChart.options.scales.y.grace = yGrace;
    powerChart.options.scales.x.min = axisMin;
    powerChart.options.scales.x.max = axisMax;
    powerChart.update('none');
    return;
  }

  const ctx = canvas.getContext('2d');
  const colors = chartColors();
  powerChart = new Chart(ctx, {
    type: 'line',
    data: {
      datasets: [
        {
          label: 'Stored EU',
          data: storedData,
          borderColor: colors.line,
          backgroundColor: colors.fill,
          fill: true,
          pointRadius: 0,
          borderWidth: 2,
          tension: 0.15,
        },
        {
          label: sizeLabel,
          data: capacityData,
          borderColor: colors.muted,
          borderDash: [4, 4],
          hidden: noCapacity,
          fill: false,
          pointRadius: 0,
          borderWidth: 1.5,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      // mode: 'index' (not 'nearest') so hovering ANYWHERE along a
      // given x-position shows both series together - with 'nearest',
      // Chart.js picks whichever single line's point is pixel-closest
      // to the cursor, so unless you're hovering exactly on the
      // Stored EU line, you'd get the Capacity tooltip instead (which
      // has no %), making the percentage seem like it never appears.
      interaction: { mode: 'index', axis: 'x', intersect: false },
      plugins: {
        // No Capacity entry for a machine without one (the wireless
        // network). dataset.hidden is only ever set above; a legend
        // click hides a line through the chart's own meta instead.
        legend: { labels: { color: colors.muted, filter: (item, data) => !data.datasets[item.datasetIndex].hidden } },
        tooltip: {
          callbacks: {
            label: (item) => {
              // Only the "Stored EU" line (dataset 0) gets a
              // percentage - pull the matching Capacity (or wireless
              // threshold) value at this same point from dataset 1 to
              // compute it against.
              // .y since each point is now a {x,y} object, not a
              // plain number, on a genuine time scale.
              if (item.datasetIndex === 0) {
                const capacity = item.chart.data.datasets[1].data[item.dataIndex].y;
                if (!(capacity > 0)) return `${item.dataset.label}: ${formatEU(item.parsed.y)} EU`;
                const pct = item.parsed.y / capacity * 100;
                return `${item.dataset.label}: ${formatEU(item.parsed.y)} EU (${pct.toFixed(1)}%)`;
              }
              return `${item.dataset.label}: ${formatEU(item.parsed.y)} EU`;
            },
          },
        },
      },
      scales: {
        x: {
          type: 'time',
          min: axisMin,
          max: axisMax,
          time: CHART_TIME_CONFIG,
          ticks: chartXTicks(colors),
          grid: { color: 'rgba(128,128,128,0.1)' },
        },
        y: {
          grace: yGrace,
          ticks: { color: colors.muted, callback: (v) => formatEU(v) },
          grid: { color: 'rgba(128,128,128,0.1)' },
        },
      },
    },
  });
}

// ---------- Renaming a machine (admins)
// The machine being renamed: the one shown when the dialog opened.
let renaming = null;

function openPowerRename() {
  if (!lastPowerData || !lastPowerData.latest) return;
  // A machine just picked whose data hasn't come yet: the one shown is
  // still the one before, so wait rather than rename that.
  if (powerMachine != null && lastPowerData.machine !== powerMachine) return;
  renaming = lastPowerData.machine;
  const input = document.getElementById('powerRenameInput');
  input.value = machineLabel(renaming);
  showPowerRenameError('');
  document.getElementById('powerRenameModal').style.display = 'flex';
  input.focus();
  input.select();
}

function closePowerRename() {
  document.getElementById('powerRenameModal').style.display = 'none';
  renaming = null;
}

function showPowerRenameError(message) {
  const el = document.getElementById('powerRenameError');
  el.textContent = message;
  el.style.display = message ? '' : 'none';
}

async function submitPowerRename() {
  if (renaming == null) return;
  const to = document.getElementById('powerRenameInput').value.trim();
  if (!to) return showPowerRenameError('Give it a name.');
  // Unchanged: nothing to do. For the unnamed machine, shown as "Main",
  // sending it would rename it to a real "Main", rewriting its whole
  // history for a name it already shows.
  if (to === machineLabel(renaming)) return closePowerRename();
  const button = document.getElementById('powerRenameSubmitBtn');
  button.disabled = true;
  try {
    const res = await fetch('/api/admin/power/machines', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ from: renaming, to }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return showPowerRenameError(data.error || 'Could not rename it.');
    closePowerRename();
    setPowerMachine(data.name);
  } catch (e) {
    showPowerRenameError('Could not reach the server.');
  } finally {
    button.disabled = false;
  }
}

export function setupPowerActions() {
  onBackdropClick('powerRenameModal', closePowerRename);
  document.getElementById('powerRenameInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') submitPowerRename();
    if (e.key === 'Escape') closePowerRename();
  });
  // A chart takes its colours when built (chartColors), so a new theme rebuilds it.
  onThemeChange(() => {
    if (!powerChart) return;
    powerChart.destroy();
    powerChart = null;
    if (lastPowerData) renderPower(lastPowerData);
  });
  delegateActions(document, {
    'power-range': (el) => setPowerRange(el.dataset.range),
    'power-machine': (el) => setPowerMachine(el.dataset.machine),
    'power-rename': () => openPowerRename(),
    'power-rename-close': () => closePowerRename(),
    'power-rename-submit': () => submitPowerRename(),
  });
}
