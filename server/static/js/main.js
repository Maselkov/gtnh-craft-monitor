// Startup - must load last: every other script only declares
// things, this one runs them.

// Before anything renders: handlers for markup the render functions
// rebuild via innerHTML.
setupImageErrorRemoval();
setupCraftsActions();
setupAdminActions();
setupCraftRequestActions();

// Reflect whatever URL the page actually loaded on (/, /crafts,
// /power, /network, or /network?item=...) rather than always
// starting on the Crafts tab regardless of how it was linked to.
(function initFromUrl() {
  const initialTab = pathToTab(location.pathname);
  if (initialTab === 'network') {
    const parsed = parseItemUrlPath(location.pathname);
    if (parsed) pendingItemFromUrl = parsed;
  }
  switchTab(initialTab, false);  // false: don't push a new history entry
                                  // over the URL we just loaded
})();

updateNotifButton();
setupNetworkTooltipEvents();
setupCraftHistoryLinks();
setupAmountInputForDevice();
// scrollLeft can change from cursor movement alone (arrow keys past
// the visible edge), with no 'input' event firing - the overlay's
// scroll position needs its own listener to stay in sync, not just
// piggyback on oninput.
document.getElementById('networkSearch').addEventListener('scroll', updateNetworkSearchHighlight);
updateNetworkSearchHighlight();
loadAuthentication().catch(() => {}).finally(() => refresh());
setInterval(refresh, 3000);
setInterval(tickSourceLine, 1000);
