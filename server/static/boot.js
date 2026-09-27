// A plain script, run before the main.js module graph. If that graph
// fails to load or link (a module missing, or an export one expects
// isn't there), nothing on the page runs - no data, no buttons - with
// the reason only in the console. This puts it on the page instead.
// main.js sets window.gcmStarted first thing, after which errors are
// the modules' own business.

function showLoadError(reason) {
  if (window.gcmStarted) return;
  const banner = document.getElementById('loadError');
  if (!banner) return;
  banner.textContent = 'The page failed to load (' + reason + '). Reload it; if that '
    + "doesn't help, clear this site's data in your browser's settings.";
  banner.classList.add('show');
}

// Uncaught errors, including a module that fails to link.
window.addEventListener('error', (event) => {
  if (event.target === window) showLoadError(event.message);
});

// A <script> or module that couldn't be fetched. Resource errors don't
// bubble, hence capture; other elements (a missing icon) are ignored.
window.addEventListener('error', (event) => {
  if (event.target instanceof HTMLScriptElement) {
    showLoadError('could not load ' + (event.target.src || 'a script'));
  }
}, true);
