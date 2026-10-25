// Webhooks admin dialog: where base-wide notifications are posted and
// which events (server/gcm/webhooks.py), and a test post. Read-only when
// the server's environment sets WEBHOOK_URLS.

import { AUTH_USER, closeSettingsMenu } from './auth.js';
import { showToast } from './craft-actions.js';
import { delegateActions, onBackdropClick } from './util.js';

let environment = false;

function eventBoxes() {
  return [...document.querySelectorAll('#webhooksModal input[data-event]')];
}

function showError(message) {
  const el = document.getElementById('webhooksError');
  el.textContent = message || '';
  el.style.display = message ? '' : 'none';
}

function show(data) {
  environment = data.environment;
  const urls = document.getElementById('webhookUrls');
  urls.value = data.urls.join('\n');
  urls.disabled = environment;
  urls.placeholder = environment
    ? `${data.count} set in the server's environment`
    : 'https://discord.com/api/webhooks/…';
  for (const box of eventBoxes()) {
    box.checked = data.events.includes(box.dataset.event);
    box.disabled = environment;
  }
  document.getElementById('webhooksSaveBtn').style.display = environment ? 'none' : '';
  let note = environment
    ? "Set in the server's environment (WEBHOOK_URLS and WEBHOOK_EVENTS), so they can't be changed here."
    : 'Notifications for the whole base, posted to a Discord channel or anything that takes a JSON POST. One URL per line.';
  if (data.hosts.length && !environment) note += ` This server only posts to ${data.hosts.join(', ')}.`;
  document.getElementById('webhooksNote').textContent = note;
}

async function openWebhooksModal() {
  if (!AUTH_USER || AUTH_USER.role !== 'admin') return;
  showError('');
  try {
    const res = await fetch('/api/admin/webhooks');
    if (!res.ok) throw new Error(`webhooks: ${res.status}`);
    show(await res.json());
  } catch (e) {
    showToast('Could not reach the server.', true);
    return;
  }
  document.getElementById('webhooksModal').style.display = 'flex';
}

export function closeWebhooksModal() {
  document.getElementById('webhooksModal').style.display = 'none';
}

// Returns whether it saved; says why not in the dialog.
async function save() {
  const body = {
    urls: document.getElementById('webhookUrls').value.split('\n'),
    events: eventBoxes().filter(box => box.checked).map(box => box.dataset.event),
  };
  try {
    const res = await fetch('/api/admin/webhooks', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showError(data.error || 'Could not save the webhooks.');
      return false;
    }
    show(data);
    showError('');
    return true;
  } catch (e) {
    showError('Could not reach the server.');
    return false;
  }
}

// Saves what's in the dialog first, so the test goes where it says.
async function testWebhooks() {
  if (!environment && !(await save())) return;
  try {
    const response = await fetch('/api/admin/webhooks/test', { method: 'POST' });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      showError(data.error || 'Could not test webhooks.');
      return;
    }
    const failed = data.results.filter(r => r.error);
    if (failed.length) {
      showError(failed.map(r => `${r.host}: ${r.error}`).join('; '));
    } else {
      showToast(`Test sent to ${data.results.length} ${data.results.length === 1 ? 'webhook' : 'webhooks'}.`);
    }
  } catch (e) {
    showError('Could not reach the server.');
  }
}

export function setupWebhooksActions() {
  delegateActions(document, {
    'open-webhooks': () => { closeSettingsMenu(); openWebhooksModal(); },
    'close-webhooks': () => closeWebhooksModal(),
    'save-webhooks': async () => {
      if (await save()) showToast('Webhooks saved.');
    },
    'test-webhooks': () => testWebhooks(),
  });
  onBackdropClick('webhooksModal', closeWebhooksModal);
}
