// Tab switching and URL <-> tab mapping.

// ---------- Tabs ----------
let activeTab = 'crafts';
let powerInterval = null;
let powerTickInterval = null;
let networkInterval = null;
let networkTickInterval = null;
let craftRequestsInterval = null;

function pathToTab(path) {
  if (path === '/power') return 'power';
  if (path === '/network' || path.startsWith(NETWORK_ITEM_PATH_PREFIX)) return 'network';
  return 'crafts';  // '/', '/crafts', or anything unrecognized
}

function tabToPath(tab) {
  if (tab === 'power') return '/power';
  if (tab === 'network') return '/network';
  return '/crafts';
}

function switchTab(tab, pushUrl) {
  if (pushUrl === undefined) pushUrl = true;
  activeTab = tab;
  document.getElementById('tabBtnCrafts').classList.toggle('active', tab === 'crafts');
  document.getElementById('tabBtnPower').classList.toggle('active', tab === 'power');
  document.getElementById('tabBtnNetwork').classList.toggle('active', tab === 'network');
  document.getElementById('craftsTab').style.display = tab === 'crafts' ? '' : 'none';
  document.getElementById('powerTab').style.display = tab === 'power' ? '' : 'none';
  document.getElementById('networkTab').style.display = tab === 'network' ? '' : 'none';

  // Leaving the network tab (or switching tabs generally) always
  // closes any open item-history popup - it doesn't make sense for
  // it to keep showing over a different tab's content.
  if (tab !== 'network') {
    closeItemHistory(false);
  }

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
    // A full scan takes a minute or two and this data doesn't change
    // fast - no point polling anywhere near as often as crafts/power.
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

window.addEventListener('popstate', () => {
  const tab = pathToTab(location.pathname);
  switchTab(tab, false);  // reacting to a nav that already happened - don't push another
  // Explicit close/open here, not left to switchTab()'s own side
  // effect - that only closes the popup when LEAVING the network
  // tab entirely (tab !== 'network'), but both /network and
  // /network/item/... map to the SAME tab, so navigating back from
  // an item page to the bare network page never triggered it: the
  // tab never "changed" from switchTab's point of view, even though
  // the popup very much needs to close.
  const parsed = (tab === 'network') ? parseItemUrlPath(location.pathname) : null;
  if (parsed) {
    pendingItemFromUrl = parsed;
    tryOpenItemFromUrl();
  } else {
    closeItemHistory(false);
  }
});

function setupTabActions() {
  delegateActions(document, {
    'switch-tab': (el) => switchTab(el.dataset.tab),
  });
}
