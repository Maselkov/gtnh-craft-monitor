// Small shared helpers: event delegation, HTML escaping for generated
// markup, and quantity formatting.

// One-time cleanup of the old all-client-side pin/completion keys
// this page used before pins moved server-side - harmless to leave,
// but nothing reads them anymore, so no reason to keep them around.
export function removeLegacyStorageKeys() {
  ['gtnhCraftMonitor.pinnedCpus', 'gtnhCraftMonitor.completedPins',
   'gtnhCraftMonitor.lastBusyByName', 'gtnhCraftMonitor.lastFinalOutputByName']
    .forEach(k => { try { localStorage.removeItem(k); } catch (e) {} });
}

// Animated icons are APNGs. Viewers who've asked their browser for reduced
// motion get each one's first frame instead (the server's ?still=1).
const REDUCED_MOTION = typeof matchMedia === 'function'
  && matchMedia('(prefers-reduced-motion: reduce)').matches;

export function iconUrl(path) {
  return `/icons?path=${encodeURIComponent(path)}${REDUCED_MOTION ? '&still=1' : ''}`;
}

// Icons drawn past the item box (a cosmic halo) are bigger images with the
// box in their middle, and the server marks their paths with how much
// bigger: <prefix>~bleed<n>/item/... (gcm/icons.py). Given the class
// .icon-bleed, such an image is scaled up around its centre, so the item
// fills the usual space and the halo spills over what's around it. Every
// bundle so far uses n = 12, which is all the CSS knows.
export function iconClass(base, path) {
  return /^[^/]*~bleed12\//.test(path || '') ? `${base} icon-bleed` : base;
}

export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// Clicks on any [data-action] element inside container - including
// elements added later by an innerHTML re-render - call
// actions[name](element, event). Arguments travel as data-*
// attributes, escaped with escapeHtml like any other attribute value,
// never spliced into JS source inside an HTML attribute.
//
// Action names must be unique page-wide: several containers can see
// the same click (everything bubbles to document), so a name
// registered twice would run twice - refused here instead.
const registeredActions = new Set();
export function delegateActions(container, actions) {
  for (const name of Object.keys(actions)) {
    if (registeredActions.has(name)) throw new Error(`data-action "${name}" registered twice`);
    registeredActions.add(name);
  }
  container.addEventListener('click', (e) => {
    const el = e.target.closest('[data-action]');
    if (!el || !container.contains(el)) return;
    const action = actions[el.dataset.action];
    if (action) action(el, e);
  });
}

// Modal overlays close on a click on the dimmed backdrop itself, not on
// anything inside the dialog.
export function onBackdropClick(overlayId, close) {
  const overlay = document.getElementById(overlayId);
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) close();
  });
}

// <img data-remove-on-error> removes itself when it fails to load (an
// icon missing from images.zip) rather than showing a broken-image
// glyph. error events don't bubble, hence the capture-phase listener.
export function setupImageErrorRemoval() {
  document.addEventListener('error', (e) => {
    if (e.target instanceof HTMLImageElement && e.target.hasAttribute('data-remove-on-error')) {
      e.target.remove();
    }
  }, true);
}
export function formatQty(n) {
  if (n == null) return '?';
  const abs = Math.abs(n);
  if (abs >= 1e9) return (n / 1e9).toFixed(2) + 'B';
  if (abs >= 1e6) return (n / 1e6).toFixed(2) + 'M';
  if (abs >= 1e3) return (n / 1e3).toFixed(1) + 'k';
  return String(Math.round(n));
}
