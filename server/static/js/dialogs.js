// Keyboard and screen-reader behaviour shared by every dialog
// (.modal-overlay). The dialogs are shown and hidden by their own code
// setting style.display, so this watches that rather than every open
// and close function calling in:
// - each box is marked up as a modal dialog, labelled by its title;
// - opening one moves focus into it, unless its own code already did
//   (the craft dialog's amount box);
// - Tab and Shift+Tab stay inside the top dialog;
// - closing one puts focus back where it was before it opened, unless
//   focus has gone on somewhere else since (another dialog opened).

import { findCopy } from './util.js';

const FOCUSABLE = 'a[href], button, input, select, textarea, summary, [tabindex]:not([tabindex="-1"])';
const openedFrom = new Map();  // overlay -> element focused before it opened
let focused = null;      // the element focused now,
let focusedBefore = null;  // and the one before it

function isShown(overlay) {
  return overlay.style.display !== 'none';
}

// The dialog on top: the highest z-index, then the latest in the page.
export function topDialog() {
  let top = null;
  let topZ = -Infinity;
  for (const overlay of document.querySelectorAll('.modal-overlay')) {
    if (!isShown(overlay)) continue;
    const z = parseInt(getComputedStyle(overlay).zIndex, 10) || 0;
    if (z >= topZ) {
      top = overlay;
      topZ = z;
    }
  }
  return top;
}

function focusables(box) {
  return [...box.querySelectorAll(FOCUSABLE)].filter(el =>
    !el.disabled && !el.closest('[hidden], [inert]') && el.getClientRects().length > 0);
}

function markUp(overlay) {
  const box = overlay.querySelector('.modal-box');
  if (!box) return;
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-modal', 'true');
  box.tabIndex = -1;  // focusable by script, so focus can wait in the dialog itself
  const title = box.querySelector('.modal-item-name, .craft-title');
  if (title) {
    if (!title.id) title.id = overlay.id + 'Title';
    box.setAttribute('aria-labelledby', title.id);
  }
}

function onShown(overlay) {
  const active = document.activeElement;
  const box = overlay.querySelector('.modal-box') || overlay;
  if (active && overlay.contains(active)) {
    // Its own code focused something in it: what had focus before that.
    openedFrom.set(overlay, focusedBefore && !overlay.contains(focusedBefore) ? focusedBefore : null);
    return;
  }
  openedFrom.set(overlay, active && active !== document.body ? active : null);
  box.focus({ preventScroll: true });
}

function onHidden(overlay) {
  const opener = openedFrom.get(overlay);
  // The Crafts tab and the ingredients grid are redrawn every few
  // seconds, so it may have been replaced by a copy of itself.
  const back = opener && !opener.isConnected ? findCopy(document, opener) : opener;
  openedFrom.delete(overlay);
  const active = document.activeElement;
  // Focus moved on to something else (another dialog's box): leave it.
  if (active && active !== document.body && !overlay.contains(active)) return;
  if (back && back.isConnected && back.getClientRects().length > 0) {
    back.focus({ preventScroll: true });
    return;
  }
  // Nothing to go back to: the dialog under it, if there is one.
  const under = topDialog();
  if (under) (under.querySelector('.modal-box') || under).focus({ preventScroll: true });
  else if (active && overlay.contains(active)) active.blur();
}

function trapTab(e) {
  if (e.key !== 'Tab' || e.ctrlKey || e.metaKey || e.altKey) return;
  const overlay = topDialog();
  if (!overlay) return;
  const box = overlay.querySelector('.modal-box') || overlay;
  const items = focusables(box);
  if (!items.length) {
    e.preventDefault();
    box.focus({ preventScroll: true });
    return;
  }
  const first = items[0];
  const last = items[items.length - 1];
  const active = document.activeElement;
  const inside = box.contains(active) && active !== box;
  if (e.shiftKey && (!inside || active === first)) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && (!inside || active === last)) {
    e.preventDefault();
    first.focus();
  }
}

export function setupDialogs() {
  const observer = new MutationObserver((records) => {
    for (const { target, oldValue } of records) {
      const wasShown = !/display:\s*none/.test(oldValue || '');
      const shown = isShown(target);
      if (shown && !wasShown) onShown(target);
      else if (!shown && wasShown) onHidden(target);
    }
  });
  for (const overlay of document.querySelectorAll('.modal-overlay')) {
    markUp(overlay);
    observer.observe(overlay, { attributes: true, attributeFilter: ['style'], attributeOldValue: true });
  }
  document.addEventListener('keydown', trapTab);
  document.addEventListener('focusin', (e) => {
    if (e.target === focused) return;
    focusedBefore = focused;
    focused = e.target;
  });
}
