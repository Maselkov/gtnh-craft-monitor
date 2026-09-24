// Small shared helpers: defensive localStorage access, HTML/JS
// escaping for generated markup, and quantity formatting.

// localStorage helpers - wrapped defensively since some browsers/modes
// (private browsing, storage disabled) throw on access rather than
// just returning null.
function lsGet(key) {
  try { return localStorage.getItem(key); } catch (e) { return null; }
}
function lsSet(key, value) {
  try { localStorage.setItem(key, value); } catch (e) { /* ignore */ }
}

// One-time cleanup of the old all-client-side pin/completion keys
// this page used before pins moved server-side - harmless to leave,
// but nothing reads them anymore, so no reason to keep them around.
['gtnhCraftMonitor.pinnedCpus', 'gtnhCraftMonitor.completedPins',
 'gtnhCraftMonitor.lastBusyByName', 'gtnhCraftMonitor.lastFinalOutputByName']
  .forEach(k => { try { localStorage.removeItem(k); } catch (e) {} });

function escapeHtml(s) {
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
function delegateActions(container, actions) {
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
function onBackdropClick(overlayId, close) {
  const overlay = document.getElementById(overlayId);
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) close();
  });
}

// <img data-remove-on-error> removes itself when it fails to load (an
// icon missing from images.zip) rather than showing a broken-image
// glyph. error events don't bubble, hence the capture-phase listener.
function setupImageErrorRemoval() {
  document.addEventListener('error', (e) => {
    if (e.target instanceof HTMLImageElement && e.target.hasAttribute('data-remove-on-error')) {
      e.target.remove();
    }
  }, true);
}
function formatQty(n) {
  if (n == null) return '?';
  const abs = Math.abs(n);
  if (abs >= 1e9) return (n / 1e9).toFixed(2) + 'B';
  if (abs >= 1e6) return (n / 1e6).toFixed(2) + 'M';
  if (abs >= 1e3) return (n / 1e3).toFixed(1) + 'k';
  return String(Math.round(n));
}
