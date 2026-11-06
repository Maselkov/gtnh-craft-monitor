// A link to one crafting job: /crafts?cpu=<name>&job=<time>, the time
// the server first saw the job (gcm/job_links.py). It's the URL of the
// job's details (the Ingredients dialog, which has a button to copy it).
// Opened, the page asks the server which job that is now: still running,
// its details open over its card, which is outlined; ended, a note above
// the CPUs says how it went (it lives outside #root, which the 3s redraw
// replaces). The link stays good after the job ends.

import { nameText } from './description.js';
import { ingredientCount, onLinkedIngredientsClosed, openIngredientsModal } from './ingredients.js';
import {
  delegateActions, escapeHtml, formatDuration, formatRelativeTime, iconClass, iconUrl, jobPath,
} from './util.js';

// A job whose end the game is still reporting is asked about again after this.
const ENDING_RETRY_MS = 3000;

let linked = null;      // {cpu, t}: the job the URL names
let found = null;       // the server's last answer about it
let shown = false;      // its card and details have been shown once
let asking = false;
let onChange = () => {};

function fromUrl() {
  if (location.pathname !== '/' && location.pathname !== '/crafts') return null;
  const params = new URLSearchParams(location.search);
  const cpu = params.get('cpu');
  const t = Number(params.get('job'));
  return cpu && Number.isInteger(t) && t > 0 ? { cpu, t } : null;
}

// changed: called when what the Crafts tab shows for the link changes
// (redraw the CPUs so the card's outline follows).
export function setupJobLink(changed) {
  onChange = changed;
  delegateActions(document.getElementById('jobLinkNote'), {
    'dismiss-job-link': () => clearJobLink(),
  });
  // Closing the details a link opened is done with the link, unless the
  // job has ended: then its note, behind them, is what's left to read.
  onLinkedIngredientsClosed(() => {
    if (found && found.state !== 'live') history.replaceState(null, '', location.href);
    else clearJobLink();
  });
}

// The page opened on, or Back/Forward landed on, a URL: follow its link
// if it has one. Not for a history entry of the details dialog, whose URL
// is its job's link too: that's the dialog reopening (tabs.js).
export function readJobLinkFromUrl() {
  const next = fromUrl();
  if (next && linked && next.cpu === linked.cpu && next.t === linked.t) return;
  linked = next;
  found = null;
  shown = false;
  renderNote();
  if (linked) ask();
}

// Leaving the link: dismissed, or another tab opened.
export function clearJobLink() {
  if (!linked) return;
  linked = null;
  found = null;
  renderNote();
  onChange();
  if (fromUrl()) history.replaceState(null, '', '/crafts');
}

// Whether a report's job is the one linked to, for its card's outline.
export function isLinkedJob(job) {
  return !!(linked && found && found.state === 'live' && job.busy
    && job.name === linked.cpu && job.started_at === found.started_at);
}

// After each redraw of the CPUs: scroll to the linked card the first
// time it's there, and ask again once the job has left it.
export function afterCraftsRender(data) {
  if (!linked || !found || found.state !== 'live') return;
  const job = (data.jobs || []).find(isLinkedJob);
  if (!job) {
    ask();
    return;
  }
  if (shown) return;
  const card = [...document.querySelectorAll('#root .card')].find(c => c.dataset.id === `cpu:${linked.cpu}`);
  if (!card) return;
  shown = true;
  // Its details, as its Ingredients button opens them, in the link's own
  // history entry, the page's first. Its card when it has none to show yet.
  if (ingredientCount(job) > 0) openIngredientsModal(job.name, data, 'link');
  else card.scrollIntoView({ block: 'center' });
}

async function ask() {
  if (asking || !linked) return;
  const asked = linked;
  asking = true;
  let answer = null;
  try {
    const res = await fetch('/api/crafts/job?' + new URLSearchParams({ cpu: asked.cpu, job: String(asked.t) }));
    if (res.ok) answer = await res.json();
  } catch (e) { /* leave the note as it was */ }
  asking = false;
  if (linked !== asked || !answer) return;
  found = answer;
  renderNote();
  onChange();
  if (found.state === 'ending') setTimeout(() => { if (linked === asked) ask(); }, ENDING_RETRY_MS);
}

function endedHtml(e) {
  const icon = e.icon
    ? `<img class="${iconClass('craft-icon', e.icon)}" src="${iconUrl(e.icon)}" alt="" loading="lazy" data-remove-on-error>`
    : '';
  const label = escapeHtml(nameText(e.itemName || 'Unknown item'));
  const name = e.internal
    ? `<span class="item-history-link" tabindex="0" role="button" data-mod="${escapeHtml(e.mod || '')}" data-internal="${escapeHtml(e.internal)}" data-damage="${e.damage != null ? escapeHtml(e.damage) : ''}" data-name="${escapeHtml(e.itemName || 'Unknown item')}" data-icon="${escapeHtml(e.icon || '')}">${label}</span>`
    : `<span>${label}</span>`;
  const cpu = `CPU ${escapeHtml(e.cpu)}`;
  const how = e.status === 'incomplete'
    ? `stopped${e.progress != null ? ` at ${escapeHtml(e.progress)}%` : ''} on ${cpu}`
    : `finished on ${cpu}`;
  const took = e.startedAt != null ? `, took ${formatDuration(e.finishedAt - e.startedAt)}` : '';
  const when = escapeHtml(new Date(e.finishedAt * 1000).toLocaleString());
  return `${icon}<span>${name} ${how} <span title="${when}">${formatRelativeTime(e.finishedAt)}</span>${took}.</span>`;
}

function renderNote() {
  const note = document.getElementById('jobLinkNote');
  const text = document.getElementById('jobLinkNoteText');
  if (!linked || !found || found.state === 'live') {
    note.hidden = true;
    return;
  }
  const cpu = `CPU ${escapeHtml(linked.cpu)}`;
  if (found.state === 'ended') {
    text.innerHTML = endedHtml(found.event);
  } else if (found.state === 'ending') {
    text.innerHTML = `<span>This craft on ${cpu} just ended&hellip;</span>`;
  } else {
    text.innerHTML = `<span>This craft on ${cpu} has ended, and how it went wasn't recorded.</span>`;
  }
  note.hidden = false;
}
