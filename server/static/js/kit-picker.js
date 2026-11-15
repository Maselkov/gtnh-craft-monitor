// The kit dialog's icon menus. Focus stays on the combobox while arrows
// move through the list, so Escape closes the menu before the dialog.
import { delegateActions, escapeHtml, iconClass, iconUrl, updateHtml } from './util.js';

function iconMarkup(icon) {
  return icon ? `<img class="${iconClass('kit-choice-icon', icon)}" src="${escapeHtml(iconUrl(icon))}" alt="">`
    : '<span class="kit-choice-icon kit-choice-placeholder" aria-hidden="true"></span>';
}

// Also used by the materials table's redraw, so its keyed combobox and
// focused input survive a change of block, count or row order.
// placeholder and footer: a searchable one's hint and the line under
// its options (the kit's multiblock picker's by default).
// action: its actions' prefix, one per setupKitPickers() (action names
// are unique page-wide).
export function kitPickerHtml({ id, label, searchable = false, inline = false,
  placeholder = 'Find a multiblock…', footer = 'Controllers in your network', action = 'kit-choice' }) {
  const safeId = escapeHtml(id);
  return `<div class="kit-choice-field">
    ${inline ? '' : '<span class="kit-choice-current-icon" aria-hidden="true"></span>'}
    <input id="${safeId}" class="kit-choice-input" role="combobox" aria-label="${escapeHtml(label)}"
      aria-expanded="false" aria-controls="${safeId}List" aria-haspopup="listbox"
      ${searchable ? `aria-autocomplete="list" placeholder="${escapeHtml(placeholder)}"` : 'readonly'}
      autocomplete="off" spellcheck="false" data-action="${action}-toggle">
    <span class="kit-choice-chevron" aria-hidden="true"></span>
    ${inline ? `<label class="kit-choice-caption" for="${safeId}">${escapeHtml(label)}</label>` : ''}
  </div>
  <div class="kit-choice-menu" popover="manual" hidden>
    <div id="${safeId}List" class="kit-choice-options" role="listbox" aria-label="${escapeHtml(label)}"></div>
    ${searchable && footer ? `<div class="kit-choice-footer">${escapeHtml(footer)}</div>` : ''}
  </div>`;
}

export function setupKitPickers(container, action = 'kit-choice') {
  const pickers = new WeakMap();
  let opened = null;

  function close(within = null) {
    if (!opened || (within && !within.contains(opened.root))) return;
    opened.close();
    opened = null;
  }

  delegateActions(container, {
    [`${action}-toggle`]: (el) => pickers.get(el.closest('.kit-choice'))?.toggle(),
    [`${action}-pick`]: (el) => pickers.get(el.closest('.kit-choice'))?.choose(el.dataset.value),
  });
  container.addEventListener('pointerdown', (e) => {
    // Selecting an option keeps focus on its combobox, including on touch.
    if (e.target.closest(`[data-action="${action}-pick"]`)) e.preventDefault();
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
  // The menus sit above the scrolling materials pane. Close them if
  // their anchor moves, while allowing the options themselves to scroll.
  container.addEventListener('scroll', (e) => {
    if (opened && !opened.menu.contains(e.target)) close();
  }, true);
  window.addEventListener('resize', () => close());

  // limit: the most options shown at once, for a long list that's
  // searched rather than scrolled through.
  function mount(root, { id, label, searchable = false, inline = false, onSelect, placeholder, footer,
    limit = Infinity }) {
    if (pickers.has(root)) return pickers.get(root);
    const safeId = escapeHtml(id);
    root.classList.add('kit-choice');
    updateHtml(root, kitPickerHtml({ id, label, searchable, inline, placeholder, footer, action }));
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
      const q = query.toLowerCase().trim();
      visible = [];
      for (const o of options) {
        if (visible.length >= limit) break;
        if (o.label.toLowerCase().includes(q)) visible.push(o);
      }
      updateHtml(list, visible.map((o, i) => `<div id="${safeId}Option${i}" role="option"
        class="kit-choice-option" aria-selected="${o.value === selected?.value}" data-key="${escapeHtml(o.value)}"
        data-action="${action}-pick" data-value="${escapeHtml(o.value)}">
        ${iconMarkup(o.icon)}<span class="kit-choice-name">${escapeHtml(o.label)}</span>
        <span class="kit-choice-check" aria-hidden="true">${o.value === selected?.value ? '✓' : ''}</span>
      </div>`).join('') || `<div class="kit-choice-empty" role="status">${escapeHtml(empty)}</div>`);
      highlight(visible.length ? Math.max(0, visible.findIndex(o => o.value === selected?.value)) : -1);
    }

    const picker = {
      root,
      menu,
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
        menu.hidePopover?.();
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
        menu.showPopover?.();
        input.setAttribute('aria-expanded', 'true');
        // Keep the popup inside the viewport when the dialog is scrolled.
        const box = root.querySelector('.kit-choice-field').getBoundingClientRect();
        const above = box.top, below = window.innerHeight - box.bottom;
        const upward = below < 220 && above > below;
        const width = Math.min(Math.max(240, box.width), window.innerWidth - 32);
        menu.style.width = width + 'px';
        menu.style.left = Math.max(16, Math.min(box.left, window.innerWidth - width - 16)) + 'px';
        menu.style.top = upward ? 'auto' : (box.bottom + 5) + 'px';
        menu.style.bottom = upward ? (window.innerHeight - box.top + 5) + 'px' : 'auto';
        list.style.maxHeight = Math.max(80, Math.min(260, (upward ? above : below) - 48)) + 'px';
        render();
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
