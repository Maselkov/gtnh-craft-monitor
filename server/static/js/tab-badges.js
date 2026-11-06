// Little status pills on the tab buttons, so what needs a look shows
// from whichever tab is open: active crafts (and finished ones not yet
// acknowledged), low stock rules, power running out.

// Power runs out within this long, or is under POWER_LOW_FRACTION full.
const POWER_SOON_SECONDS = 3600;
const POWER_LOW_FRACTION = 0.1;
// The wireless network (no capacity) has no "full" to fall under, and
// holds far more: warn when it empties within a day.
const WIRELESS_SOON_SECONDS = 86400;
// A machine's last reading counts for this long (power.js's FILL_STALE_SECONDS).
const POWER_FRESH_SECONDS = 300;

// The unnamed machine is the one reported before machines had names.
// Here rather than in power.js, which imports this module.
export function machineLabel(name) {
  return name || 'Main';
}

// A badge is {count, tone, label}, or null for none. count may be
// null for a plain dot; tone is '' (neutral), 'done' or 'alert'.
export function craftsBadge(jobs, completed) {
  const busy = (jobs || []).filter(j => j.busy).length;
  const finished = (completed || []).length;
  if (!busy && !finished) return null;
  const parts = [];
  if (busy) parts.push(`${busy} crafting`);
  if (finished) parts.push(`${finished} finished`);
  return { count: busy || null, tone: finished ? 'done' : '', label: parts.join(', ') };
}

export function stockBadge(lowCount) {
  return lowCount ? { count: lowCount, tone: 'alert', label: `${lowCount} low` } : null;
}

// latest and trend are the tab's default machine's (latest.name its
// name); machines are all of them, for an LSC in wireless mode that isn't
// the default (the wireless network is, while it reports). The badge's
// machine is the one it's about.
export function powerBadge(latest, trend, machines = [], now = Date.now() / 1000) {
  if (!latest) return null;
  const alert = (label, machine = latest.name) => ({ count: null, tone: 'alert', label, machine });
  const wireless = !(latest.capacity > 0);
  const soonSeconds = wireless ? WIRELESS_SOON_SECONDS : POWER_SOON_SECONDS;
  const refilled = latest.wireless_threshold > 0;  // by the network, every 5 minutes
  if (!refilled && trend && trend.seconds_to_empty != null && trend.seconds_to_empty < soonSeconds) {
    return alert(wireless ? 'running out within a day' : 'running out within the hour');
  }
  // An LSC in wireless mode drained under its threshold between 5-minute
  // refills: it needs more, or higher-tier, capacitors.
  const drained = (machines || []).find(m => m.wireless_threshold > 0 && m.stored != null
    && now - m.t <= POWER_FRESH_SECONDS && m.stored / m.wireless_threshold < POWER_LOW_FRACTION);
  if (drained) return alert(`${machineLabel(drained.name)} under 10% of its wireless threshold`, drained.name);
  // That LSC's capacity says nothing: it keeps only its threshold.
  if (!wireless && !(latest.wireless_threshold > 0) && latest.stored / latest.capacity < POWER_LOW_FRACTION) {
    return alert('under 10% full');
  }
  // Another machine running low while the wireless network is the
  // default: only an LSC whose sensor says it's NOT in wireless mode. One
  // on an older script doesn't say, and may be in wireless mode, where
  // its capacity means nothing.
  const low = (machines || []).find(m => m.wireless_mode === false && m.capacity > 0 && m.stored != null
    && now - m.t <= POWER_FRESH_SECONDS && m.stored / m.capacity < POWER_LOW_FRACTION);
  if (low) return alert(`${machineLabel(low.name)} under 10% full`, low.name);
  return null;
}

const TAB_BUTTONS = { crafts: 'tabBtnCrafts', power: 'tabBtnPower', network: 'tabBtnNetwork' };

export function setTabBadge(tab, badge) {
  const button = document.getElementById(TAB_BUTTONS[tab]);
  let el = button.querySelector('.tab-badge');
  if (!badge) {
    if (el) el.remove();
    return;
  }
  if (!el) {
    el = document.createElement('span');
    button.append(el);
  }
  el.className = 'tab-badge' + (badge.tone ? ' ' + badge.tone : '') + (badge.count == null ? ' dot' : '');
  el.textContent = badge.count == null ? '' : String(badge.count);
  el.setAttribute('aria-label', badge.label);
  el.title = badge.label;
}
