// Craft request and cancel modals, pending craft requests.

// ---------- Craft request modal ----------
let craftRequestTarget = null;

function openCraftRequestModal(it) {
  craftRequestTarget = it;
  document.getElementById('craftRequestName').textContent = it.name || '?';
  const iconEl = document.getElementById('craftRequestIcon');
  if (it.icon) {
    iconEl.src = '/icons?path=' + encodeURIComponent(it.icon);
    iconEl.style.display = '';
  } else {
    iconEl.style.display = 'none';
  }
  document.getElementById('craftRequestAmount').value = 1;
  document.getElementById('craftRequestAmountPreview').style.display = 'none';
  const errorEl = document.getElementById('craftRequestError');
  errorEl.style.display = 'none';
  const btn = document.getElementById('craftRequestSubmitBtn');
  btn.disabled = false;
  btn.textContent = 'Request';
  document.getElementById('craftRequestModal').style.display = 'flex';
}

function closeCraftRequestModal() {
  document.getElementById('craftRequestModal').style.display = 'none';
  craftRequestTarget = null;
}

function showToast(message, isError) {
  const container = document.getElementById('toastContainer');
  const el = document.createElement('div');
  el.className = 'toast' + (isError ? ' error' : '');
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => el.remove(), 5000);
}

// ---------- Cancel craft ----------
let cancelConfirmTarget = null;  // cpu_name currently targeted by the confirm modal
const pendingCancelCpus = new Set();  // cpu_names with an in-flight
                                       // cancel request - drives the
                                       // spinner/disabled state on
                                       // that specific card

function openCancelConfirmModal(cpuName) {
  cancelConfirmTarget = cpuName;
  document.getElementById('cancelConfirmSub').textContent = 'CPU ' + cpuName;
  const errorEl = document.getElementById('cancelConfirmError');
  errorEl.style.display = 'none';
  const btn = document.getElementById('cancelConfirmSubmitBtn');
  btn.disabled = false;
  btn.textContent = 'Cancel craft';
  document.getElementById('cancelConfirmModal').style.display = 'flex';
}

function closeCancelConfirmModal() {
  document.getElementById('cancelConfirmModal').style.display = 'none';
  cancelConfirmTarget = null;
}

async function submitCancelConfirm() {
  if (!cancelConfirmTarget) return;
  const cpuName = cancelConfirmTarget;
  const errorEl = document.getElementById('cancelConfirmError');
  errorEl.style.display = 'none';

  if (!AUTH_USER) {
    errorEl.textContent = 'Sign in before cancelling a craft.';
    errorEl.style.display = 'block';
    return;
  }

  const btn = document.getElementById('cancelConfirmSubmitBtn');
  btn.disabled = true;
  btn.textContent = 'Cancelling…';

  try {
    const res = await fetch('/api/craft/cancel', {
      method: 'POST',
      headers: userHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ cpu_name: cpuName }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      errorEl.textContent = data.error || 'Cancel request failed.';
      errorEl.style.display = 'block';
      btn.disabled = false;
      btn.textContent = 'Cancel craft';
      return;
    }
    closeCancelConfirmModal();
    pendingCancelCpus.add(cpuName);
    if (lastData) render(lastData);  // show the spinner state on the card right away
    pollCancelResult(data.id, cpuName);
  } catch (e) {
    errorEl.textContent = 'Could not reach server.';
    errorEl.style.display = 'block';
    btn.disabled = false;
    btn.textContent = 'Cancel craft';
  }
}

// cancel() resolves synchronously on Lua's side (confirmed from
// source) - the only latency here is Lua's own poll interval picking
// the request up, so this should resolve within a couple seconds in
// practice. Still bounded (~15s) rather than polling forever, in
// case craft_monitor.lua isn't running or the network hiccups.
async function pollCancelResult(id, cpuName, attemptsLeft) {
  if (attemptsLeft === undefined) attemptsLeft = 15;
  try {
    const res = await fetch('/api/craft/cancel/' + id, { headers: userHeaders() });
    const data = await res.json();
    if (data.status === 'resolved') {
      pendingCancelCpus.delete(cpuName);
      if (data.success) {
        showToast('Craft cancelled - items returned to the network.');
      } else {
        showToast(data.reason || 'Could not cancel - it may have already finished.', true);
      }
      if (lastData) render(lastData);
      return;
    }
  } catch (e) {
    // keep trying until attempts run out
  }
  if (attemptsLeft > 1) {
    setTimeout(() => pollCancelResult(id, cpuName, attemptsLeft - 1), 1000);
  } else {
    pendingCancelCpus.delete(cpuName);
    showToast('No response from the game - check in-game, or try again.', true);
    if (lastData) render(lastData);
  }
}

// Same fix as the img[src^="/icons?"] CSS rule above, for browsers
// (Android Chrome among them) where -webkit-touch-callout alone
// doesn't suppress the native long-press "save/copy image" menu -
// this covers those by directly blocking the contextmenu event
// itself, which is what that menu actually fires through on a
// touch-and-hold, not just a real right-click. Delegated on
// document rather than attached per-icon, since icons are rebuilt
// wholesale on every render (search, sort, the 3s poll) - per-
// element listeners would just be discarded each time anyway.
document.addEventListener('contextmenu', (e) => {
  if (e.target.closest('img[src^="/icons?"]')) {
    e.preventDefault();
  }
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    if (document.getElementById('settingsMenu').style.display !== 'none') {
      closeSettingsMenu();
    } else if (document.getElementById('craftRequestModal').style.display !== 'none') {
      closeCraftRequestModal();
    } else if (document.getElementById('cancelConfirmModal').style.display !== 'none') {
      closeCancelConfirmModal();
    } else if (document.getElementById('itemHistoryModal').style.display !== 'none') {
      closeItemHistory();
    } else if (document.getElementById('adminUsersModal').style.display !== 'none') {
      closeAdminUsersModal();
    }
  } else if (e.key === 'Enter') {
    // Only the craft-request modal - cancelConfirm's primary action
    // is destructive, so Enter shouldn't accidentally trigger it,
    // and itemHistory has no single "submit" action to speak of.
    if (document.getElementById('craftRequestModal').style.display !== 'none') {
      e.preventDefault();
      submitCraftRequest();
    }
  }
});

// Shows "= 68,000,000" below the field, but ONLY when it would tell
// the user something they don't already know from looking at their
// own typed text - a bare "500" evaluating to 500 shows nothing;
// "10k" or "4+3*2" does, since those resolve to something visibly
// different from what's on screen. This single rule naturally
// covers metric prefixes, math expressions, AND a bare decimal like
// "4.5" (which rounds to something that isn't "4.5") without
// needing to separately enumerate "does it contain +/-/*// or k/m/b".
function updateCraftRequestAmountPreview() {
  const input = document.getElementById('craftRequestAmount');
  const previewEl = document.getElementById('craftRequestAmountPreview');
  const raw = input.value.trim();

  if (!raw) {
    previewEl.style.display = 'none';
    return;
  }

  const result = evaluateAmountExpression(raw);
  if (!result.ok) {
    // Only flash an error state for something that actually LOOKS
    // like an attempted expression (has an operator/paren) - a
    // single number still being typed (e.g. a lone "4." mid-decimal)
    // shouldn't show a scary error for a normal, incomplete keystroke.
    if (/[+\-*/()]/.test(raw)) {
      previewEl.textContent = 'Invalid expression';
      previewEl.className = 'modal-amount-preview modal-amount-preview-error';
      previewEl.style.display = '';
    } else {
      previewEl.style.display = 'none';
    }
    return;
  }

  const rounded = Math.round(result.value);
  if (String(rounded) === raw) {
    previewEl.style.display = 'none';
    return;
  }
  previewEl.textContent = '= ' + rounded.toLocaleString();
  previewEl.className = 'modal-amount-preview';
  previewEl.style.display = '';
}

async function submitCraftRequest() {
  if (!craftRequestTarget) return;
  const errorEl = document.getElementById('craftRequestError');
  errorEl.style.display = 'none';

  const evaluated = evaluateAmountExpression(document.getElementById('craftRequestAmount').value);
  const amount = evaluated.ok ? Math.round(evaluated.value) : null;
  if (!evaluated.ok || !amount || amount <= 0) {
    errorEl.textContent = evaluated.ok
      ? 'Enter a quantity of at least 1 (e.g. 500, 10k, or 4+3*2 on desktop).'
      : 'Invalid expression: ' + evaluated.error;
    errorEl.style.display = 'block';
    return;
  }

  if (!AUTH_USER) {
    errorEl.textContent = 'Sign in before requesting a craft.';
    errorEl.style.display = 'block';
    return;
  }

  const btn = document.getElementById('craftRequestSubmitBtn');
  btn.disabled = true;
  btn.textContent = 'Submitting…';

  try {
    const res = await fetch('/api/craft/request', {
      method: 'POST',
      headers: userHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({
        label: craftRequestTarget.name,
        mod: craftRequestTarget.mod,
        internal: craftRequestTarget.internal,
        damage: craftRequestTarget.damage,
        amount: amount,
        kind: craftRequestTarget.kind || 'item',
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      errorEl.textContent = err.error || 'Request failed.';
      errorEl.style.display = 'block';
      btn.disabled = false;
      btn.textContent = 'Request';
      return;
    }
    closeCraftRequestModal();
    fetchCraftRequests();  // show it in the pending list immediately,
                            // not on the next automatic poll
  } catch (e) {
    errorEl.textContent = 'Could not reach server.';
    errorEl.style.display = 'block';
    btn.disabled = false;
    btn.textContent = 'Request';
  }
}

// ---------- Pending craft requests ----------
let craftRequests = [];

async function fetchCraftRequests() {
  try {
    const res = await fetch('/api/craft/requests', { headers: userHeaders() });
    const data = await res.json();
    craftRequests = data.requests || [];
    renderCraftRequests();
  } catch (e) {
    // leave whatever's currently shown in place, just skip this tick
  }
}

function renderCraftRequests() {
  const el = document.getElementById('craftRequestsSection');
  if (craftRequests.length === 0) {
    el.innerHTML = '';
    return;
  }
  el.innerHTML = craftRequests.map(r => {
    const icon = r.icon
      ? `<img class="craft-request-icon" src="/icons?path=${encodeURIComponent(r.icon)}" alt="" loading="lazy" data-remove-on-error>`
      : '';
    const isFailed = r.status === 'failed';
    const statusText = isFailed
      ? escapeHtml(r.reason || 'Request failed')
      : 'Waiting for acknowledgement…';
    const dismissBtn = isFailed
      ? `<button class="craft-request-dismiss" data-action="dismiss-request" data-request-id="${escapeHtml(r.id)}" title="Dismiss">&times;</button>`
      : '';
    return `
      <div class="craft-request-card${isFailed ? ' failed' : ''}">
        ${icon}
        <div class="craft-request-info">
          <div class="craft-request-name">${escapeHtml(r.label)} ×${r.amount}</div>
          <div class="craft-request-status${isFailed ? ' failed' : ''}">${statusText}</div>
        </div>
        ${dismissBtn}
      </div>
    `;
  }).join('');
}

function setupCraftRequestActions() {
  delegateActions(document.getElementById('craftRequestsSection'), {
    'dismiss-request': (el) => dismissCraftRequest(el.dataset.requestId),
  });
}

async function dismissCraftRequest(id) {
  try {
    await fetch(`/api/craft/requests/${id}/dismiss`, { method: 'POST', headers: userHeaders() });
  } catch (e) { /* ignore */ }
  fetchCraftRequests();
}

// Metric-prefix amounts (10k, 1.5m) only make sense with a real
// keyboard - on a touch device, the native numeric keypad a
// type="number" input pops up is better UX than free text entry
// would be, so this leaves it alone there and only switches to
// text (allowing the 'k'/'m'/'b' suffix) on a fine-pointer/desktop
// device. inputmode="decimal" keeps a numeric-leaning virtual
// keyboard on any touch device that DOES end up with the text
// input somehow (a hybrid device with a mouse, say), rather than
// the full alphabetic keyboard a bare type="text" would invite.
function setupAmountInputForDevice() {
  const isDesktop = !(window.matchMedia && window.matchMedia('(pointer: coarse)').matches);
  if (!isDesktop) return;
  const input = document.getElementById('craftRequestAmount');
  input.type = 'text';
  input.setAttribute('inputmode', 'decimal');
  input.title = 'Accepts metric shorthand, e.g. 10k or 1.5m';
}
