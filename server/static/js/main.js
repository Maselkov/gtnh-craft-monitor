// Entry point (the only <script> in index.html). Every other module
// only declares things - importing one has no side effects - and this
// one runs them.

import { loadAuthentication, setupAdminActions, setupAuthControls } from './auth.js';
import {
  setupAmountInputForDevice,
  setupCraftDialogActions,
  setupCraftRequestActions,
} from './craft-actions.js';
import { refresh, setupCraftsActions, tickSourceLine, updateNotifButton } from './crafts.js';
import {
  parseItemUrlPath,
  setPendingItemFromUrl,
  setupCraftHistoryLinks,
  setupHistoryActions,
} from './history.js';
import {
  setupNetworkActions,
  setupNetworkTooltipEvents,
  updateNetworkSearchHighlight,
} from './network.js';
import { setupPowerActions } from './power.js';
import { pathToTab, setupTabActions, switchTab } from './tabs.js';
import { removeLegacyStorageKeys, setupImageErrorRemoval } from './util.js';

// Before anything renders: every event handler on the page. index.html
// and the render functions only carry data-action attributes.
removeLegacyStorageKeys();
setupImageErrorRemoval();
setupAuthControls();
setupAdminActions();
setupCraftsActions();
setupTabActions();
setupPowerActions();
setupNetworkActions();
setupHistoryActions();
setupCraftDialogActions();
setupCraftRequestActions();

// Reflect whatever URL the page actually loaded on (/, /crafts,
// /power, /network, or /network?item=...) rather than always
// starting on the Crafts tab regardless of how it was linked to.
(function initFromUrl() {
  const initialTab = pathToTab(location.pathname);
  if (initialTab === 'network') {
    const parsed = parseItemUrlPath(location.pathname);
    if (parsed) setPendingItemFromUrl(parsed);
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
