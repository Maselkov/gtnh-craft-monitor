// Little status pills on the tab buttons, so what needs a look shows
// from whichever tab is open: active crafts (and finished ones not yet
// acknowledged), low stock rules, power running out.

// Power runs out within this long, or is under POWER_LOW_FRACTION full.
const POWER_SOON_SECONDS = 3600;
const POWER_LOW_FRACTION = 0.1;

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

export function powerBadge(latest, trend) {
  if (!latest) return null;
  const low = latest.capacity > 0 && latest.stored / latest.capacity < POWER_LOW_FRACTION;
  const soon = trend && trend.seconds_to_empty != null && trend.seconds_to_empty < POWER_SOON_SECONDS;
  if (!low && !soon) return null;
  return { count: null, tone: 'alert', label: soon ? 'running out within the hour' : 'under 10% full' };
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
