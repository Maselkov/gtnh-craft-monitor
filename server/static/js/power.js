// Power tab: readings, trend and chart.

import { CHART_RANGE_SECONDS_JS, CHART_TIME_CONFIG } from './history.js';
import { delegateActions } from './util.js';

// ---------- Power chart ----------
let powerRange = 'day';
let powerChart = null;
let lastPowerData = null;  // separate from crafts' lastData on purpose -
                            // power comes from a different Lua script
                            // (power_monitor.lua, reading gt_machine),
                            // polled on its own 15s cadence, so its
                            // freshness has nothing to do with crafts'
                            // me_controller-based "updated Xs ago" line

function setPowerRange(range) {
  powerRange = range;
  document.querySelectorAll('.range-btn').forEach(btn => {
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

function formatEU(n) {
  if (n == null) return '?';
  const abs = Math.abs(n);
  if (abs >= 1e12) return (n / 1e12).toFixed(2) + 'T';
  if (abs >= 1e9) return (n / 1e9).toFixed(2) + 'G';
  if (abs >= 1e6) return (n / 1e6).toFixed(2) + 'M';
  if (abs >= 1e3) return (n / 1e3).toFixed(2) + 'k';
  return String(Math.round(n));
}

export async function fetchPower() {
  try {
    const res = await fetch('/api/power?range=' + powerRange);
    const data = await res.json();
    lastPowerData = data;
    renderPower(data);
    tickPowerSourceLine();
  } catch (e) {
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
  const avgIn = latest.avg_eu_in_5s;
  const avgOut = latest.avg_eu_out_5s;
  if (avgIn == null || avgOut == null) return '';

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

function renderPower(data) {
  const points = data.points || [];
  const empty = document.getElementById('powerEmpty');
  const canvas = document.getElementById('powerChart');

  if (data.latest) {
    const pct = data.latest.capacity > 0 ? (data.latest.stored / data.latest.capacity * 100) : 0;
    const trendHtml = ' ' + buildPowerTrendHtml(data.latest);
    document.getElementById('powerCurrent').innerHTML =
      `<span class="big">${formatEU(data.latest.stored)} EU</span> / ${formatEU(data.latest.capacity)} EU (${pct.toFixed(1)}%)${trendHtml}`;
  } else {
    document.getElementById('powerCurrent').textContent = 'No readings yet';
  }

  if (points.length === 0) {
    empty.style.display = 'block';
    canvas.style.display = 'none';
    return;
  }
  empty.style.display = 'none';
  canvas.style.display = '';

  // {x, y} point objects, not a separate labels array - matches the
  // item-history chart's own time-scale approach (see that chart's
  // comment for the full reasoning): each point carries its own
  // real position rather than relying on index-alignment with a
  // parallel label list, which is what caused uneven, misleading
  // tick spacing on a category axis.
  const storedData = points.map(p => ({ x: p.t * 1000, y: p.stored }));
  const capacityData = points.map(p => ({ x: p.t * 1000, y: p.capacity }));

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
    powerChart.options.scales.x.min = axisMin;
    powerChart.options.scales.x.max = axisMax;
    powerChart.update('none');
    return;
  }

  const ctx = canvas.getContext('2d');
  powerChart = new Chart(ctx, {
    type: 'line',
    data: {
      datasets: [
        {
          label: 'Stored EU',
          data: storedData,
          borderColor: '#5fb3ff',
          backgroundColor: 'rgba(95,179,255,0.15)',
          fill: true,
          pointRadius: 0,
          borderWidth: 2,
          tension: 0.15,
        },
        {
          label: 'Capacity',
          data: capacityData,
          borderColor: 'rgba(140,140,140,0.5)',
          borderDash: [4, 4],
          fill: false,
          pointRadius: 0,
          borderWidth: 1,
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
        legend: { labels: { color: '#8a8f98' } },
        tooltip: {
          callbacks: {
            label: (item) => {
              // Only the "Stored EU" line (dataset 0) gets a
              // percentage - pull the matching Capacity value at
              // this same point from dataset 1 to compute it against.
              // .y since each point is now a {x,y} object, not a
              // plain number, on a genuine time scale.
              if (item.datasetIndex === 0) {
                const capacity = item.chart.data.datasets[1].data[item.dataIndex].y;
                const pct = capacity > 0 ? (item.parsed.y / capacity * 100) : 0;
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
          ticks: { color: '#8a8f98', maxRotation: 0, autoSkip: true },
          grid: { color: 'rgba(128,128,128,0.1)' },
        },
        y: { ticks: { color: '#8a8f98', callback: (v) => formatEU(v) }, grid: { color: 'rgba(128,128,128,0.1)' } },
      },
    },
  });
}

export function setupPowerActions() {
  delegateActions(document, {
    'power-range': (el) => setPowerRange(el.dataset.range),
  });
}
