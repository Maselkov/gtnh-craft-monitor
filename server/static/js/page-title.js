// The browser tab's title and favicon follow the tab badges
// (tab-badges.js), so a page left in a background tab says when
// something wants a look: "(2) Power · GTNH Monitor", and a dot on the
// favicon (the server draws it: gcm/app_icons.py's badged()).

export const SITE_TITLE = 'GTNH Monitor';

// The title: how many things want attention, from the tab badges
// ({crafts, power, network}), then the view that's open.
export function pageTitle(view, badges = {}) {
  const count = Object.values(badges).reduce((sum, b) => sum + ((b && b.attention) || 0), 0);
  return (count ? `(${count}) ` : '') + (view ? `${view} · ` : '') + SITE_TITLE;
}

// The favicon's dot: 'alert' for any alert badge, else 'done' for
// finished crafts to acknowledge, else null.
export function faviconDot(badges = {}) {
  const all = Object.values(badges);
  if (all.some(b => b && b.tone === 'alert')) return 'alert';
  if (all.some(b => b && b.tone === 'done')) return 'done';
  return null;
}

// The craft dialog's view: its progress while it runs, so a tab left on
// one craft shows how it's going, and that it ended once it has.
export function craftView(name, percent, ended) {
  if (ended) return `Finished · ${name}`;
  return percent != null ? `${percent}% ${name}` : name;
}

// The favicon URL with that dot: the page's own href plus dot=.
export function dotHref(href, dot) {
  if (!dot) return href;
  return href + (href.includes('?') ? '&' : '?') + 'dot=' + dot;
}

const badges = {};
// The view's name: the open tab's, unless a dialog is open over it. The
// item dialog can open over the ingredients dialog, so it comes first.
const views = { tab: '', craft: '', item: '' };
let shownDot = null;
let iconLinks = null;  // [[link, its own href]], read on the first change

function apply() {
  if (typeof document === 'undefined') return;  // the unit tests
  const title = pageTitle(views.item || views.craft || views.tab, badges);
  if (document.title !== title) document.title = title;

  const dot = faviconDot(badges);
  if (dot === shownDot) return;
  shownDot = dot;
  if (!iconLinks) {
    iconLinks = Array.from(document.querySelectorAll('link[rel="icon"]'), link => [link, link.getAttribute('href')]);
  }
  for (const [link, href] of iconLinks) link.setAttribute('href', dotHref(href, dot));
}

export function notePageBadge(tab, badge) {
  badges[tab] = badge;
  apply();
}

// which is 'tab', 'craft' (the ingredients dialog) or 'item' (the item
// dialog); an empty label for a dialog that closed.
export function setPageView(which, label) {
  views[which] = label || '';
  apply();
}
