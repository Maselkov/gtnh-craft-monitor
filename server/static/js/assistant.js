// The MCP server dialog. Operators and admins get the /mcp address and a
// link to the guide; admins also the house rules added to every
// assistant's instructions (server/gcm/mcp/instructions.py) and the
// latest tool calls assistants made.

import { AUTH_USER, closeSettingsMenu, isOperator } from './auth.js';
import { showToast } from './craft-actions.js';
import { delegateActions, docsUrl, escapeHtml, formatRelativeTime, onBackdropClick } from './util.js';

let maxRules = 4000;

function showError(message) {
  const el = document.getElementById('assistantError');
  el.textContent = message || '';
  el.style.display = message ? '' : 'none';
}

function updateCounter() {
  const length = document.getElementById('assistantRules').value.length;
  const counter = document.getElementById('assistantRulesCount');
  counter.textContent = `${length.toLocaleString()} / ${maxRules.toLocaleString()}`;
  counter.classList.toggle('over', length > maxRules);
}

function callRowHtml(call) {
  const who = call.user ? escapeHtml(call.user) : 'a deleted user';
  const error = call.is_error
    ? `<div class="assistant-call-error">${escapeHtml(call.error || 'error')}</div>`
    : '';
  return `<div class="assistant-call${call.is_error ? ' failed' : ''}">
    <div class="assistant-call-head"><b>${escapeHtml(call.tool)}</b>
      <span class="assistant-call-meta">${who} · ${formatRelativeTime(call.at)} · ${call.ms} ms</span></div>
    <div class="assistant-call-args">${escapeHtml(call.arguments)}</div>${error}
  </div>`;
}

async function loadCalls() {
  const list = document.getElementById('assistantCalls');
  try {
    const res = await fetch('/api/admin/assistant/calls?limit=50');
    if (!res.ok) throw new Error(`calls: ${res.status}`);
    const { calls } = await res.json();
    list.innerHTML = calls.length
      ? calls.map(callRowHtml).join('')
      : '<div class="modal-note">No assistant has used the tools yet.</div>';
  } catch (e) {
    list.innerHTML = '<div class="modal-note">Could not load the calls.</div>';
  }
}

async function getJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  return res.json();
}

async function openAssistantModal() {
  if (!isOperator()) return;
  const admin = AUTH_USER.role === 'admin';
  showError('');
  try {
    const [server, settings] = await Promise.all([
      getJson('/api/mcp-server'),
      admin ? getJson('/api/admin/assistant') : null,
    ]);
    document.getElementById('assistantUrl').value = server.mcp_url;
    if (settings) {
      maxRules = settings.max_rules;
      document.getElementById('assistantRules').value = settings.rules;
    }
  } catch (e) {
    showToast('Could not reach the server.', true);
    return;
  }
  document.getElementById('assistantAdmin').hidden = !admin;
  document.getElementById('assistantModal').style.display = 'flex';
  if (admin) {
    updateCounter();
    loadCalls();
  }
}

export function closeAssistantModal() {
  document.getElementById('assistantModal').style.display = 'none';
}

async function saveRules() {
  const rules = document.getElementById('assistantRules').value;
  if (rules.length > maxRules) {
    showError(`House rules can be at most ${maxRules.toLocaleString()} characters.`);
    return;
  }
  try {
    const res = await fetch('/api/admin/assistant/rules', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rules }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showError(data.error || 'Could not save the house rules.');
      return;
    }
    document.getElementById('assistantRules').value = data.rules;
    updateCounter();
    showError('');
    showToast('House rules saved. Assistants get them when they next connect.');
  } catch (e) {
    showError('Could not reach the server.');
  }
}

async function copyUrl() {
  const input = document.getElementById('assistantUrl');
  try {
    await navigator.clipboard.writeText(input.value);
    showToast('Address copied.');
  } catch (e) {
    // No clipboard access (plain http, or refused): select it instead.
    input.focus();
    input.select();
  }
}

export function setupAssistantActions() {
  delegateActions(document, {
    'open-assistant': () => { closeSettingsMenu(); openAssistantModal(); },
    'close-assistant': () => closeAssistantModal(),
    'save-assistant-rules': () => saveRules(),
    'copy-assistant-url': () => copyUrl(),
    'refresh-assistant-calls': () => loadCalls(),
  });
  document.getElementById('assistantRules').addEventListener('input', updateCounter);
  document.getElementById('assistantDocsLink').href = docsUrl('using.md#ai-assistants');
  onBackdropClick('assistantModal', closeAssistantModal);
}
