// Tab switching and URL <-> tab mapping.

import { fetchCraftRequests } from './craft-actions.js';
import { lastData } from './crafts.js';
import {
  hideItemHistory,
  NETWORK_ITEM_PATH_PREFIX,
  openItemHistory,
  parseItemUrlPath,
  setPendingItemFromUrl,
  tryOpenItemFromUrl,
} from './history.js';
import { hideIngredientsModal, ingredientsCpu, openIngredientsModal } from './ingredients.js';
import { fetchNetwork, fetchNetworkPins, tickNetworkSourceLine } from './network.js';
import { fetchPower, powerMachineFromUrl, powerPath, tickPowerSourceLine } from './power.js';
import { fetchStockRules } from './stock.js';
import { delegateActions } from './util.js';

// ---------- Tabs ----------
export let activeTab = 'crafts';
let powerInterval = null;
let powerTickInterval = null;
let networkInterval = null;
let networkTickInterval = null;
let craftRequestsInterval = null;

export function pathToTab(path) {
  if (path === '/power') return 'power';
  if (path === '/network' || path.startsWith(NETWORK_ITEM_PATH_PREFIX)) return 'network';
  return 'crafts';  // '/', '/crafts', or anything unrecognized
}

function tabToPath(tab) {
  if (tab === 'power') return powerPath();
  if (tab === 'network') return '/network';
  return '/crafts';
}

export function switchTab(tab, pushUrl) {
  if (pushUrl === undefined) pushUrl = true;
  // A dialog belongs to the tab it was opened over.
  if (tab !== activeTab) {
    hideItemHistory();
    hideIngredientsModal();
  }
  activeTab = tab;
  document.getElementById('tabBtnCrafts').classList.toggle('active', tab === 'crafts');
  document.getElementById('tabBtnPower').classList.toggle('active', tab === 'power');
  document.getElementById('tabBtnNetwork').classList.toggle('active', tab === 'network');
  document.getElementById('craftsTab').style.display = tab === 'crafts' ? '' : 'none';
  document.getElementById('powerTab').style.display = tab === 'power' ? '' : 'none';
  document.getElementById('networkTab').style.display = tab === 'network' ? '' : 'none';

  if (pushUrl) {
    history.pushState({ view: tab }, '', tabToPath(tab));
  }

  if (tab === 'power') {
    fetchPower();
    if (!powerInterval) powerInterval = setInterval(fetchPower, 15000);
    if (!powerTickInterval) powerTickInterval = setInterval(tickPowerSourceLine, 1000);
  } else {
    if (powerInterval) { clearInterval(powerInterval); powerInterval = null; }
    if (powerTickInterval) { clearInterval(powerTickInterval); powerTickInterval = null; }
  }

  if (tab === 'network') {
    fetchNetwork();
    fetchNetworkPins();
    fetchStockRules();
    // A full scan takes up to 10 minutes and this data doesn't change
    // fast - no point polling anywhere near as often as crafts/power.
    // Stock rules only change with a scan (or an auto-craft's answer);
    // main.js polls them on every tab, for the tab badge.
    if (!networkInterval) networkInterval = setInterval(fetchNetwork, 60000);
    if (!networkTickInterval) networkTickInterval = setInterval(tickNetworkSourceLine, 1000);
    // Craft requests are the opposite - they should resolve within
    // seconds, matching craft_monitor.lua's own ~1.5s request-polling
    // loop, so this polls much faster than the network snapshot does.
    fetchCraftRequests();
    if (!craftRequestsInterval) craftRequestsInterval = setInterval(fetchCraftRequests, 2000);
  } else {
    if (networkInterval) { clearInterval(networkInterval); networkInterval = null; }
    if (networkTickInterval) { clearInterval(networkTickInterval); networkTickInterval = null; }
    if (craftRequestsInterval) { clearInterval(craftRequestsInterval); craftRequestsInterval = null; }
  }
}

// A tab the browser kept in the background has had its timers slowed to
// about once a minute - fetch the open tab's data straight away when it
// comes back rather than wait for them.
export function refreshActiveTab() {
  if (activeTab === 'power') fetchPower();
  if (activeTab === 'network') {
    fetchNetwork();
    fetchCraftRequests();
  }
}

// Back or Forward landed on a history entry: show its tab and the
// dialogs it had open. An entry the page pushed says which (tab; cpu
// for the ingredients dialog; item for the item dialog, which may be
// over any tab); one it didn't - the page's first, or a link typed in -
// goes by the URL alone.
function applyHistoryState(state) {
  state = state || {};
  const tab = state.tab || pathToTab(location.pathname);
  if (tab === 'power') powerMachineFromUrl();
  switchTab(tab, false);  // reacting to a nav that already happened - don't push another

  if (state.cpu) {
    if (ingredientsCpu() !== state.cpu) openIngredientsModal(state.cpu, lastData, false);
  } else {
    hideIngredientsModal();
  }

  if (state.view === 'item' && state.item) {
    openItemHistory(state.item, false);
    return;
  }
  hideItemHistory();
  const parsed = (tab === 'network') ? parseItemUrlPath(location.pathname) : null;
  if (parsed) {
    setPendingItemFromUrl(parsed);
    tryOpenItemFromUrl();
  }
}

export function setupTabActions() {
  window.addEventListener('popstate', (e) => applyHistoryState(e.state));

  document.addEventListener('keydown', onSlashKey);
  delegateActions(document, {
    'switch-tab': (el) => {
      switchTab(el.dataset.tab);
      if (el.dataset.tab === 'network') focusNetworkSearch();
    },
  });
}

// "/" jumps to the network search from anywhere, as on many sites -
// unless the key is going into a text field or a dialog is open.
function onSlashKey(e) {
  if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return;
  const target = e.target;
  if (target.closest && target.closest('input, textarea, select, [contenteditable]')) return;
  const dialogOpen = [...document.querySelectorAll('.modal-overlay')].some(m => m.style.display !== 'none');
  if (dialogOpen) return;
  e.preventDefault();
  if (activeTab !== 'network') switchTab('network');
  const search = document.getElementById('networkSearch');
  search.focus();
  search.select();
}

// Clicking over to the network tab is almost always to look something
// up, so the search box takes the keyboard straight away. Not on touch
// devices, where focusing it would throw the on-screen keyboard over
// the list before the user has even asked to type.
export function focusNetworkSearch() {
  if (window.matchMedia && window.matchMedia('(pointer: coarse)').matches) return;
  document.getElementById('networkSearch').focus();
}
