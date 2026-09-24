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

// A JS string literal that's safe inside a double-quoted inline
// handler attribute: onclick="fn(${jsArg(value)})". JSON.stringify
// escapes quotes/backslashes for the JS parser; escapeHtml then
// protects the attribute (the browser decodes it back before JS runs).
function jsArg(s) {
  return escapeHtml(JSON.stringify(String(s)));
}
function formatQty(n) {
  if (n == null) return '?';
  const abs = Math.abs(n);
  if (abs >= 1e9) return (n / 1e9).toFixed(2) + 'B';
  if (abs >= 1e6) return (n / 1e6).toFixed(2) + 'M';
  if (abs >= 1e3) return (n / 1e3).toFixed(1) + 'k';
  return String(Math.round(n));
}
