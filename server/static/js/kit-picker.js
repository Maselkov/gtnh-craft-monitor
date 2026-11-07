// The kit dialog's icon menus. Focus stays on the combobox while arrows
// move through the list, so Escape closes the menu before the dialog.
import { delegateActions, escapeHtml, iconClass, iconUrl, updateHtml } from './util.js';

function iconMarkup(icon) {
  return icon ? `<img class="${iconClass('kit-choice-icon', icon)}" src="${escapeHtml(iconUrl(icon))}" alt="">`
    : '<span class="kit-choice-icon kit-choice-placeholder" aria-hidden="true"></span>';
}

// Also used by the materials table's redraw, so its keyed combobox and
// focused input survive a change of block, count or row order.
export function kitPickerHtml({ id, label, searchable = false, inline = false }) {
  const safeId = escapeHtml(id);
  return `<div class="kit-choice-field">
    ${inline ? '' : '<span class="kit-choice-current-icon" aria-hidden="true"></span>'}
    <input id="${safeId}" class="kit-choice-input" role="combobox" aria-label="${escapeHtml(label)}"
      aria-expanded="false" aria-controls="${safeId}List" aria-haspopup="listbox"
      ${searchable ? 'aria-autocomplete="list" placeholder="Find a multiblock…"' : 'readonly'}
      autocomplete="off" spellcheck="false" data-action="kit-choice-toggle">
    <span class="kit-choice-chevron" aria-hidden="true"></span>
    ${inline ? `<label class="kit-choice-caption" for="${safeId}">${escapeHtml(label)}</label>` : ''}
  </div>
  <div class="kit-choice-menu" hidden>
    <div id="${safeId}List" class="kit-choice-options" role="listbox" aria-label="${escapeHtml(label)}"></div>
    ${searchable ? '<div class="kit-choice-footer">Controllers in your network</div>' : ''}
  </div>`;
}

export function setupKitPickers(container) {
  const pickers = new WeakMap();
  let opened = null;

  function close(within = null) {
    if (!opened || (within && !within.contains(opened.root))) return;
    opened.close();
    opened = null;
  }

  delegateActions(container, {
    'kit-choice-toggle': (el) => pickers.get(el.closest('.kit-choice'))?.toggle(),
    'kit-choice-pick': (el) => pickers.get(el.closest('.kit-choice'))?.choose(el.dataset.value),
  });
  container.addEventListener('pointerdown', (e) => {
    // Selecting an option keeps focus on its combobox, including on touch.
    if (e.target.closest('[data-action="kit-choice-pick"]')) e.preventDefault();
  });
  document.addEventListener('click', (e) => {
    if (opened && !opened.root.contains(e.target)) close();
  });
  container.addEventListener('focusout', (e) => {
    if (opened?.root.contains(e.target) && !opened.root.contains(e.relatedTarget)) close();
  });
  container.addEventListener('keydown', (e) => {
    const picker = pickers.get(e.target.closest('.kit-choice'));
    if (picker) picker.keydown(e);
  });

  function mount(root, { id, label, searchable = false, inline = false, onSelect }) {
    if (pickers.has(root)) return pickers.get(root);
    const safeId = escapeHtml(id);
    root.classList.add('kit-choice');
    updateHtml(root, kitPickerHtml({ id, label, searchable, inline }));
    const input = root.querySelector('input');
    const menu = root.querySelector('.kit-choice-menu');
    const list = root.querySelector('[role="listbox"]');
    let options = [], visible = [], selected = null, active = -1;
    let empty = 'No matching multiblocks.';
    let query = '', typeahead = '', typedAt = 0;

    function showSelected() {
      input.value = selected?.label || '';
      const icon = root.querySelector('.kit-choice-current-icon');
      if (icon) updateHtml(icon, selected?.icon ? iconMarkup(selected.icon) : '');
      root.classList.toggle('has-icon', !inline && !!selected?.icon);
    }

    function highlight(index) {
      active = index;
      list.querySelectorAll('[role="option"]').forEach((el, i) => el.classList.toggle('active', i === index));
      const el = list.children[index];
      if (el?.getAttribute('role') === 'option') {
        input.setAttribute('aria-activedescendant', el.id);
        el.scrollIntoView({ block: 'nearest' });
      } else input.removeAttribute('aria-activedescendant');
    }

    function render() {
      visible = options.filter(o => o.label.toLowerCase().includes(query.toLowerCase().trim()));
      updateHtml(list, visible.map((o, i) => `<div id="${safeId}Option${i}" role="option"
        class="kit-choice-option" aria-selected="${o.value === selected?.value}" data-key="${escapeHtml(o.value)}"
        data-action="kit-choice-pick" data-value="${escapeHtml(o.value)}">
        ${iconMarkup(o.icon)}<span class="kit-choice-name">${escapeHtml(o.label)}</span>
        <span class="kit-choice-check" aria-hidden="true">${o.value === selected?.value ? '✓' : ''}</span>
      </div>`).join('') || `<div class="kit-choice-empty" role="status">${escapeHtml(empty)}</div>`);
      highlight(visible.length ? Math.max(0, visible.findIndex(o => o.value === selected?.value)) : -1);
    }

    const picker = {
      root,
      setOptions(items, message = 'No matching multiblocks.') {
        options = items;
        empty = message;
        if (opened === picker) render();
      },
      setValue(value, fallback = null) {
        selected = options.find(o => o.value === value) || fallback;
        if (opened !== picker) showSelected();
        else render();
      },
      close() {
        menu.hidden = true;
        input.setAttribute('aria-expanded', 'false');
        input.removeAttribute('aria-activedescendant');
        showSelected();
      },
      open() {
        if (opened === picker) return;
        close();
        opened = picker;
        query = '';
        menu.hidden = false;
        input.setAttribute('aria-expanded', 'true');
        render();
        // Keep the popup inside the viewport when the dialog is scrolled.
        const box = input.getBoundingClientRect();
        const above = box.top, below = window.innerHeight - box.bottom;
        const upward = below < 220 && above > below;
        menu.classList.toggle('above', upward);
        list.style.maxHeight = Math.max(80, Math.min(260, (upward ? above : below) - 48)) + 'px';
      },
      toggle() {
        if (!searchable && opened === picker) close();
        else picker.open();
        input.focus();
      },
      choose(value) {
        const option = options.find(o => o.value === value);
        if (!option) return;
        selected = option;
        close();
        input.focus();
        onSelect(option.value);
      },
      keydown(e) {
        if (e.key === 'Escape' && opened === picker) {
          e.preventDefault();
          e.stopPropagation();
          close();
        } else if (e.key === 'Tab') close();
        else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
          e.preventDefault();
          if (opened !== picker) picker.open();
          else if (visible.length) highlight((active + (e.key === 'ArrowDown' ? 1 : -1) + visible.length) % visible.length);
        } else if (e.key === 'Enter' || (!searchable && e.key === ' ')) {
          e.preventDefault();
          if (opened === picker && visible[active]) picker.choose(visible[active].value);
          else picker.open();
        } else if (opened === picker && (!searchable || e.ctrlKey) && ['Home', 'End'].includes(e.key)) {
          e.preventDefault();
          highlight(e.key === 'Home' ? 0 : visible.length - 1);
        } else if (!searchable && e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault();
          typeahead = Date.now() - typedAt > 700 ? e.key : typeahead + e.key;
          typedAt = Date.now();
          picker.open();
          const index = visible.findIndex(o => o.label.toLowerCase().startsWith(typeahead.toLowerCase()));
          if (index >= 0) highlight(index);
        }
      },
    };
    input.addEventListener('input', () => {
      picker.open();
      query = input.value;
      render();
    });
    input.addEventListener('focus', () => { if (searchable) input.select(); });
    pickers.set(root, picker);
    return picker;
  }
  return { mount, close };
}
