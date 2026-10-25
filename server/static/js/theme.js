// The settings menu's Theme: light, dark, or the system's ("auto").
// Remembered per browser; boot.js applies it before first paint.

import { delegateActions } from './util.js';

const THEME_KEY = 'gtnhCraftMonitor.theme';

function currentTheme() {
  try {
    const theme = localStorage.getItem(THEME_KEY);
    return theme === 'light' || theme === 'dark' ? theme : 'auto';
  } catch (e) { return 'auto'; }
}

function showTheme(theme) {
  document.querySelectorAll('[data-action="set-theme"]').forEach((button) => {
    button.setAttribute('aria-checked', String(button.dataset.themeChoice === theme));
  });
}

function applyTheme(theme) {
  if (theme === 'auto') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  try {
    if (theme === 'auto') localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, theme);
  } catch (e) { /* per-viewer nicety only */ }
  showTheme(theme);
}

export function setupTheme() {
  showTheme(currentTheme());
  delegateActions(document, {
    'set-theme': (el) => applyTheme(el.dataset.themeChoice),
  });
}
