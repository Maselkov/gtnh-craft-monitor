// Crafts tab: CPU status polling and rendering, pins, completion
// notifications.

import { AUTH_USER } from './auth.js';
import { openCancelConfirmModal, pendingCancelCpus } from './craft-actions.js';
import { delegateActions, escapeHtml, iconClass, iconUrl } from './util.js';

// Toggle-open state survives across the 3s auto-refresh (which
// rebuilds the DOM from scratch), keyed by CPU name for ingredient
// panels and a single flag for the idle-CPU section.
const openIngredients = new Set();
let idleSectionOpen = false;

// pinnedCpus and completedPins are now just local caches of what the
// server told us on the last poll - reassigned wholesale each refresh
// rather than mutated incrementally, since the server is the source
// of truth now.
let pinnedCpus = new Set();
let completedPins = [];

// For signing out - other modules can read these but not reassign them.
export function clearPins() {
  pinnedCpus = new Set();
  completedPins = [];
}

// Tracks which completion ids have already shown a desktop
// Cross-tab notification claim, via localStorage (shared across ALL
// tabs of this origin, unlike sessionStorage which is per-tab) - the
// FIRST tab to see a new completion "claims" it here before actually
// firing the Notification, so a second tab (two tabs of this page
// open at once is completely ordinary) doesn't also notify for the
// exact same thing. Confirmed as a real, well-supported cause of
// duplicate notifications - refreshPinsAndCompletions() is only
// ever called from ONE recurring place (the main 3s refresh() loop),
// entirely unrelated to the network tab's own scan polling, so a
// "new scan" isn't a plausible second cause here.
const LS_NOTIFIED_COMPLETION_IDS = 'gtnhCraftMonitor.notifiedCompletionIds';

function loadClaimedIds() {
  try {
    const raw = localStorage.getItem(LS_NOTIFIED_COMPLETION_IDS);
    return raw ? new Set(JSON.parse(raw)) : new Set();
  } catch (e) {
    return new Set();
  }
}
function saveClaimedIds(set) {
  try {
    localStorage.setItem(LS_NOTIFIED_COMPLETION_IDS, JSON.stringify(Array.from(set)));
  } catch (e) { /* ignore */ }
}

// Seeded from the FIRST completions fetch after this page loaded,
// with every id already present marked as "already handled" WITHOUT
// ever firing a Notification for it - this is what makes a reload
// never re-notify for something that was already pending before
// this page started polling. A plain refresh and actually closing
// and reopening the tab are deliberately treated identically here
// (an earlier version of this logic tried to tell them apart via
// sessionStorage vs a fresh context, reasoning a "genuinely new
// session" re-notifying for what's still pending was fine - that
// was a real, reported wrong call: only a completion that arrives
// AFTER this baseline is established, i.e. the craft status
// genuinely changed while this page was already open, should ever
// trigger a real notification, regardless of how the page came to
// be freshly loaded).
let seenCompletionIds = null;  // null until the baseline fetch resolves

export let lastData = null;  // cached so pin/unpin can re-render immediately
                       // instead of waiting up to 3s for the next poll
let lastFetchAt = null;  // client-side Date.now() of the last successful fetch

export function setupCraftsActions() {
  delegateActions(document, {
    'toggle-notifications': () => toggleNotifications(),
  });
  const root = document.getElementById('root');
  delegateActions(root, {
    'toggle-pin': (el) => togglePin(el.dataset.cpu),
    'cancel-craft': (el) => openCancelConfirmModal(el.dataset.cpu),
    'acknowledge': (el) => acknowledgePin(el.dataset.completionId),
    'acknowledge-all': () => acknowledgeAllPins(),
  });
  // Remembers which <details> are open so the 3s re-render can restore
  // them. toggle doesn't bubble, so catch it on the way down.
  root.addEventListener('toggle', (e) => {
    const details = e.target;
    if (details.matches('details.ingredients')) {
      if (details.open) openIngredients.add(details.dataset.key); else openIngredients.delete(details.dataset.key);
    } else if (details.matches('details.idle-group')) {
      idleSectionOpen = details.open;
    }
  }, true);
}

async function togglePin(name) {
  const isPinned = pinnedCpus.has(name);
  try {
    if (isPinned) {
      await fetch('/api/pins/unpin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cpu_name: name }),
      });
    } else {
      const res = await fetch('/api/pins', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cpu_name: name }),
      });
      if (!res.ok) {
        // Most likely: the CPU went idle between this card rendering
        // and you clicking pin (the button is disabled for idle CPUs,
        // but that's a rendered-a-few-seconds-ago snapshot, not a
        // live guarantee). Just refresh silently rather than pinning.
        await refreshPinsAndCompletions();
        if (lastData) render(lastData);
        return;
      }
    }
  } catch (e) {
    return;
  }
  await refreshPinsAndCompletions();
  if (lastData) render(lastData);
}

async function acknowledgePin(id) {
  try {
    await fetch(`/api/completions/${encodeURIComponent(id)}/ack`, { method: 'POST' });
  } catch (e) { /* ignore */ }
  await refreshPinsAndCompletions();
  if (lastData) render(lastData);
}

async function acknowledgeAllPins() {
  try {
    await fetch('/api/completions/ack-all', { method: 'POST' });
  } catch (e) { /* ignore */ }
  await refreshPinsAndCompletions();
  if (lastData) render(lastData);
}

export async function refreshPinsAndCompletions() {
  if (!AUTH_USER) {
    pinnedCpus = new Set();
    completedPins = [];
    return;
  }
  try {
    const [pinsRes, completionsRes] = await Promise.all([
      fetch('/api/pins'),
      fetch('/api/completions'),
    ]);
    const pinsData = await pinsRes.json();
    const completionsData = await completionsRes.json();
    pinnedCpus = new Set(pinsData.pins || []);
    completedPins = completionsData.completions || [];
    notifyNewCompletions(completedPins);
  } catch (e) {
    // leave whatever we last had in place
  }
}

function notifyNewCompletions(completions) {
  const currentIds = new Set(completions.map(c => c.id));

  if (seenCompletionIds === null) {
    // First call since this page loaded - establish the baseline
    // only, no notifications fired for anything already pending.
    seenCompletionIds = currentIds;
    return;
  }

  const claimed = loadClaimedIds();
  let claimedChanged = false;

  for (const c of completions) {
    if (!seenCompletionIds.has(c.id)) {
      seenCompletionIds.add(c.id);
      // Genuinely new since this page's baseline - but still only
      // actually notify if no OTHER tab has already claimed this
      // exact completion id first.
      if (!claimed.has(c.id)) {
        claimed.add(c.id);
        claimedChanged = true;
        notifyCraftDone(c.id, c.itemName, c.status);
      }
    }
  }

  // Prune ids no longer pending (acknowledged/dismissed) from BOTH
  // sets - ids are never reused, so there's nothing to gain by
  // tracking them indefinitely once they can't come back.
  for (const id of Array.from(seenCompletionIds)) {
    if (!currentIds.has(id)) seenCompletionIds.delete(id);
  }
  for (const id of Array.from(claimed)) {
    if (!currentIds.has(id)) {
      claimed.delete(id);
      claimedChanged = true;
    }
  }

  if (claimedChanged) saveClaimedIds(claimed);
}

function formatRelativeTime(seconds) {
  const diffSec = Math.max(0, Math.round(Date.now() / 1000 - seconds));
  if (diffSec < 60) return diffSec + 's ago';
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return diffMin + 'm ago';
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return diffHr + 'h ago';
  return Math.round(diffHr / 24) + 'd ago';
}

// The switch in the settings menu. Browsers don't let a page revoke
// its own notification permission, so "off" is this app's own flag
// on top of it. Nothing stored means the pre-switch behaviour: on
// exactly when permission was already granted.
const LS_NOTIFICATIONS = 'gtnhCraftMonitor.notifications';

// Android Chrome throws on `new Notification()` from a page, and iOS
// only shows notifications through a service worker - so register
// one when the browser has them and show notifications through it.
let swRegistration = Promise.resolve(null);

export function setupNotifications() {
  if ('serviceWorker' in navigator && window.isSecureContext) {
    swRegistration = navigator.serviceWorker.register('/sw.js').catch(() => null);
  }
  updateNotifToggle();
}

function notificationsOn() {
  return !notificationsBlocker() && Notification.permission === 'granted' && notificationsWanted();
}

function base64UrlBytes(text) {
  const base64 = text.replace(/-/g, '+').replace(/_/g, '/');
  return Uint8Array.from(atob(base64 + '='.repeat((4 - base64.length % 4) % 4)), c => c.charCodeAt(0));
}

function postJson(url, body) {
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

// Web Push (gcm/push.py): with the switch on, this browser's push
// subscription is saved on the server under the signed-in user, so a
// finished craft reaches it with no tab open. Called on sign-in and
// whenever the switch changes; safe to call any time.
export async function syncPushSubscription() {
  const registration = await swRegistration;
  if (!registration || !('PushManager' in window) || !AUTH_USER) return;
  try {
    let subscription = await registration.pushManager.getSubscription();
    if (!notificationsOn()) {
      if (subscription) await forgetSubscription(subscription);
      return;
    }
    const { publicKey } = await (await fetch('/api/push/key')).json();
    const key = base64UrlBytes(publicKey);
    // One made for another server key (the data directory was reset,
    // say) can never be delivered to - replace it.
    const existingKey = subscription && subscription.options.applicationServerKey;
    if (existingKey && new Uint8Array(existingKey).join() !== key.join()) {
      await subscription.unsubscribe();
      subscription = null;
    }
    if (!subscription) {
      subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
    }
    await postJson('/api/push/subscribe', subscription.toJSON());
  } catch (e) {
    // No push then - the open page still notifies on its own.
  }
}

// On sign-out: pushes for this user must stop reaching this browser.
export async function dropPushSubscription() {
  const registration = await swRegistration;
  if (!registration || !('PushManager' in window)) return;
  try {
    const subscription = await registration.pushManager.getSubscription();
    if (subscription) await forgetSubscription(subscription);
  } catch (e) { /* ignore */ }
}

async function forgetSubscription(subscription) {
  await postJson('/api/push/unsubscribe', { endpoint: subscription.endpoint }).catch(() => {});
  await subscription.unsubscribe();
}

function notificationsWanted() {
  let stored = null;
  try { stored = localStorage.getItem(LS_NOTIFICATIONS); } catch (e) { /* ignore */ }
  if (stored === 'off') return false;
  if (stored === 'on') return true;
  return 'Notification' in window && Notification.permission === 'granted';
}

function setNotificationsWanted(on) {
  try { localStorage.setItem(LS_NOTIFICATIONS, on ? 'on' : 'off'); } catch (e) { /* ignore */ }
}

// Why the switch can't be turned on here, or '' if it can.
function notificationsBlocker() {
  // Checked first: over plain http:// browsers hide the API entirely,
  // and "not supported" would send people looking for the wrong fix.
  if (!window.isSecureContext) return 'Needs HTTPS';
  if (!('Notification' in window)) {
    // iOS Safari has no notifications in a normal tab, only once the
    // page is added to the home screen and opened from there.
    const ios = /iPad|iPhone|iPod/.test(navigator.userAgent)
      || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    return ios ? 'Add to Home Screen first' : 'Not supported here';
  }
  if (Notification.permission === 'denied') return 'Blocked in browser settings';
  return '';
}

export function updateNotifToggle() {
  const toggle = document.getElementById('notifToggle');
  const blocker = notificationsBlocker();
  const on = notificationsOn();
  toggle.setAttribute('aria-checked', on ? 'true' : 'false');
  toggle.disabled = Boolean(blocker);
  document.getElementById('notifNote').textContent = blocker;
}

async function toggleNotifications() {
  if (notificationsBlocker()) return;
  // Go by what the switch shows, which is what the click meant.
  if (document.getElementById('notifToggle').getAttribute('aria-checked') === 'true') {
    setNotificationsWanted(false);
  } else {
    // Browsers require this to be triggered by a direct user gesture
    // (this click). It resolves straight away if already granted.
    const permission = await Notification.requestPermission();
    if (permission === 'granted') setNotificationsWanted(true);
  }
  updateNotifToggle();
  await syncPushSubscription();
}

async function notifyCraftDone(id, itemName, status) {
  if (!notificationsOn()) return;
  const what = itemName || 'A pinned craft';
  const [title, body] = status === 'incomplete'
    ? ['Craft stopped', what + ' stopped before finishing (cancelled or interrupted).']
    : ['Craft finished', what + ' is done crafting.'];
  const options = {
    body,
    tag: 'craft-completion-' + id,
    icon: '/icons?path=item%2Fappliedenergistics2%2Ftile.BlockInterface~0.png',
  };
  try {
    const registration = await swRegistration;
    if (registration) {
      await registration.showNotification(title, options);
    } else {
      new Notification(title, options);
    }
  } catch (e) {
    // ignore - some browsers throw if called outside a gesture in odd states
  }
}

export async function refresh() {
  try {
    const craftsPromise = fetch('/api/crafts');
    await refreshPinsAndCompletions();
    const res = await craftsPromise;
    const data = await res.json();
    lastData = data;
    lastFetchAt = Date.now();
    render(data);
    tickSourceLine();
  } catch (e) {
    document.getElementById('sourceLine').textContent = 'Could not reach server.';
  }
}

// Ticks the "updated Xs ago" text every second using elapsed
// client-side time, independent of the 3s data poll - otherwise the
// display only advances once every 3s (whenever refresh() happens to
// run) and visibly jumps instead of counting smoothly.
export function tickSourceLine() {
  const sourceLine = document.getElementById('sourceLine');
  if (!lastData) return;
  if (!lastData.source) {
    sourceLine.textContent = 'Waiting for data from the game...';
    return;
  }
  let age = lastData.age_seconds;
  if (age != null && lastFetchAt != null) {
    age += (Date.now() - lastFetchAt) / 1000;
  }
  const ageDisplay = age != null ? Math.round(age) : '?';
  sourceLine.textContent = `Source: ${lastData.source} - updated ${ageDisplay}s ago`;
}

function renderItemList(label, list) {
  if (!Array.isArray(list) || list.length === 0) return '';
  const rows = list.map(it => {
    const icon = it.icon
      ? `<img class="${iconClass('item-icon', it.icon)}" src="${iconUrl(it.icon)}" alt="" loading="lazy" data-remove-on-error>`
      : '';
    const linkAttrs = `data-mod="${escapeHtml(it.mod || '')}" data-internal="${escapeHtml(it.internal || '')}" `
      + `data-damage="${it.damage != null ? it.damage : ''}" data-name="${escapeHtml(it.name || '?')}" `
      + `data-icon="${escapeHtml(it.icon || '')}"`;
    return `<div class="item">
      <span class="item-left">${icon}<span class="item-name item-history-link" ${linkAttrs}>${escapeHtml(it.name || '?')}</span></span>
      <span class="item-qty">${escapeHtml(String(it.size ?? ''))}</span>
    </div>`;
  }).join('');
  return `<div class="section-label">${label}</div>${rows}`;
}

function renderCard(job) {
  // Progress/ingredients/title all gated on job.busy, not just on
  // whether the underlying fields happen to be present - AE2 can
  // report busy=false for a poll while progress_percent/pending/
  // stored/final_output still briefly hold the previous job's data
  // (a transient internal-buffer lag on AE2's side, confirmed NOT a
  // real "still working" signal - it self-corrects within one poll,
  // unlike a genuinely suspended job which would stay that way).
  // Showing any of that on a card already labeled IDLE is actively
  // misleading regardless of why the stale data is there, so all
  // three are tied to the same busy flag used for grouping instead
  // of being decided independently per-field.
  const hasProgress = job.busy && job.progress_percent != null;
  const progressBar = hasProgress ? `
    <div class="progress-track"><div class="progress-fill" data-progress="${escapeHtml(job.progress_percent)}"></div></div>
    <div class="progress-label">${escapeHtml(job.progress_percent)}% &middot; ${escapeHtml(job.steps_done ?? '?')}/${escapeHtml(job.steps_total ?? '?')} steps done</div>
  ` : '';

  const body = renderItemList('Active', job.active) + renderItemList('Pending', job.pending) + renderItemList('Stored', job.stored);
  const itemCount = (job.active?.length || 0) + (job.pending?.length || 0) + (job.stored?.length || 0);
  const key = 'ingredients:' + job.name;
  const isOpen = openIngredients.has(key);
  const isPinned = pinnedCpus.has(job.name);
  const canPin = job.busy || isPinned;  // pinning only ever makes sense for a busy CPU

  const title = job.busy
    ? (job.final_output
        ? `${job.final_output_icon ? `<img class="${iconClass('craft-icon', job.final_output_icon)}" src="${iconUrl(job.final_output_icon)}" alt="" loading="lazy" data-remove-on-error>` : ''}<span class="item-history-link" data-mod="${escapeHtml(job.final_output_mod || '')}" data-internal="${escapeHtml(job.final_output_internal || '')}" data-damage="${job.final_output_damage != null ? job.final_output_damage : ''}" data-name="${escapeHtml(job.final_output)}" data-icon="${escapeHtml(job.final_output_icon || '')}">${escapeHtml(job.final_output)}</span>`
        : `<span class="craft-title-muted">Crafting job (no monitor tile)</span>`)
    : `<span class="craft-title-muted">Idle</span>`;

  const ingredientsBlock = (job.busy && itemCount > 0) ? `
    <details class="ingredients" ${isOpen ? 'open' : ''} data-key="${escapeHtml(key)}">
      <summary>Ingredients (${itemCount})</summary>
      <div class="ingredients-body">${body}</div>
    </details>
  ` : '';

  const pinTitle = isPinned ? 'Unpin' : (canPin ? 'Pin - notify me when this finishes' : 'Only a busy CPU can be pinned');

  const isOperator = AUTH_USER && (AUTH_USER.role === 'operator' || AUTH_USER.role === 'admin');
  const isCancelPending = pendingCancelCpus.has(job.name);
  const cancelBtn = (job.busy && isOperator)
    ? `<button class="cancel-btn" ${isCancelPending ? 'disabled' : ''} data-action="cancel-craft" data-cpu="${escapeHtml(job.name)}" title="${isCancelPending ? 'Cancelling…' : 'Cancel this craft'}">${isCancelPending ? '&#8987;' : '&times;'}</button>`
    : '';

  return `
    <div class="card">
      <div class="card-head">
        <div>
          <div class="craft-title">${title}</div>
          <div class="cpu-id">CPU ${escapeHtml(job.name || '?')} &middot; storage ${job.storage ?? '?'} &middot; coprocessors ${job.coprocessors ?? '?'}</div>
        </div>
        <div class="head-right">
          <button class="pin-btn ${isPinned ? 'pinned' : ''}" ${canPin ? '' : 'disabled'} data-action="toggle-pin" data-cpu="${escapeHtml(job.name)}" title="${pinTitle}">&#128204;</button>
          ${cancelBtn}
          <div class="badge ${job.busy ? 'busy' : 'idle'}">${job.busy ? 'BUSY' : 'IDLE'}</div>
        </div>
      </div>
      ${progressBar}
      ${ingredientsBlock}
    </div>
  `;
}

function renderCompletedCard(entry) {
  const icon = entry.icon
    ? `<img class="${iconClass('craft-icon', entry.icon)}" src="${iconUrl(entry.icon)}" alt="" loading="lazy" data-remove-on-error>`
    : '';
  // Deliberately item-first, no CPU reference - which CPU happened to
  // run this is irrelevant to what you're acknowledging.
  const title = entry.itemName ? escapeHtml(entry.itemName) : 'Unknown item';
  const statusBadge = entry.status === 'incomplete'
    ? '<span class="status-badge incomplete">Incomplete</span>'
    : '';
  return `
    <div class="card completed-card">
      <div class="card-head">
        <div>
          <div class="craft-title">${icon}<span>${title}</span>${statusBadge}</div>
          <div class="cpu-id">Finished ${formatRelativeTime(entry.finishedAt)}</div>
        </div>
        <button class="ack-btn" data-action="acknowledge" data-completion-id="${escapeHtml(entry.id)}" title="Acknowledge">&#10003;</button>
      </div>
    </div>
  `;
}

export function render(data) {
  const banner = document.getElementById('staleBanner');
  banner.classList.toggle('show', !!data.stale);

  const root = document.getElementById('root');
  const jobs = data.jobs || [];

  // Note: no busy->idle transition detection here anymore - the
  // server does that now (see _process_craft_transitions in app.py),
  // which is what lets a completion that happens while every browser
  // tab is closed still get caught, and lets each user have their
  // own independent pins rather than one shared list. This function
  // just renders whatever pinnedCpus/completedPins refreshPinsAndCompletions()
  // last fetched.

  // Sorted newest-first so the most recently finished thing is the
  // first thing you see.
  const sortedCompleted = completedPins.slice().sort((a, b) => b.finishedAt - a.finishedAt);
  const completedSection = sortedCompleted.length > 0 ? `
    <section class="group">
      <div class="group-heading completed-heading">
        <span>&#10003; Finished (${sortedCompleted.length})</span>
        <button class="ack-all-btn" data-action="acknowledge-all">Acknowledge all</button>
      </div>
      <div class="grid">${sortedCompleted.map(renderCompletedCard).join('')}</div>
    </section>
  ` : '';

  if (jobs.length === 0) {
    root.innerHTML = completedSection + '<div class="empty">No crafting CPUs reported yet.</div>';
    return;
  }

  const pinnedJobs = jobs.filter(j => pinnedCpus.has(j.name));
  const unpinned = jobs.filter(j => !pinnedCpus.has(j.name));
  const activeJobs = unpinned.filter(j => j.busy);
  const idleJobs = unpinned.filter(j => !j.busy);

  const pinnedSection = pinnedJobs.length > 0 ? `
    <section class="group">
      <div class="group-heading">&#128204; Pinned (${pinnedJobs.length})</div>
      <div class="grid">${pinnedJobs.map(renderCard).join('')}</div>
    </section>
  ` : '';

  const activeSection = activeJobs.length > 0 ? `
    <section class="group">
      <div class="group-heading">Active (${activeJobs.length})</div>
      <div class="grid">${activeJobs.map(renderCard).join('')}</div>
    </section>
  ` : (pinnedJobs.length === 0 ? `<div class="empty">No CPUs currently crafting.</div>` : '');

  const idleSection = idleJobs.length > 0 ? `
    <details class="idle-group" ${idleSectionOpen ? 'open' : ''}>
      <summary>Idle CPUs (${idleJobs.length})</summary>
      <div class="grid">${idleJobs.map(renderCard).join('')}</div>
    </details>
  ` : '';

  root.innerHTML = completedSection + pinnedSection + activeSection + idleSection;
  // Widths are set through the DOM rather than an inline style attribute in
  // the markup, which the Content-Security-Policy blocks. Done in the
  // same task as the innerHTML, so the new bars' first computed width
  // is already right and the width transition doesn't animate.
  for (const bar of root.querySelectorAll('.progress-fill[data-progress]')) {
    bar.style.width = `${Number(bar.dataset.progress)}%`;
  }
}
