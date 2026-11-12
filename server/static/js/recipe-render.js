// Shared recipe rendering. Pattern checks use static stacks; the viewer
// opts into navigation and grid slots. No setup on import.
import { escapeHtml, formatQty, iconClass, iconUrl, updateHtml } from './util.js';

// GTNH 2.9's GTValues.TIER_COLORS, including its high-tier decoration.
const TIER_STYLE = {
  ULV: 'mc-c', LV: 'mc-2', MV: 'mc-6', HV: 'mc-e', EV: 'mc-8', IV: 'mc-9',
  LuV: 'mc-d', ZPM: 'mc-b', UV: 'mc-2 mc-underline', UHV: 'mc-4 mc-underline',
  UEV: 'mc-5 mc-underline', UIV: 'mc-1 mc-bold mc-underline',
  UMV: 'mc-c mc-bold mc-underline', UXV: 'mc-4 mc-bold mc-underline', MAX: 'mc-f mc-bold mc-underline',
};

function integerText(n) { return BigInt(n).toLocaleString('en-US'); }

// GTNHlib's default NumberFormatUtil: scientific notation from one trillion.
function energyText(n) {
  const value = BigInt(n);
  return value >= 1000000000000n || value <= -1000000000000n
    ? Number(value).toExponential(2).replace('e+', 'e').replace(/\.?(0+)(?=e)/, '')
    : integerText(value);
}

function gameColour(code) {
  const value = parseInt(code, 16), bright = value & 8 ? 85 : 0;
  return `rgb(${(value & 4 ? 170 : 0) + bright + (value === 6 ? 85 : 0)}, ${
    (value & 2 ? 170 : 0) + bright}, ${(value & 1 ? 170 : 0) + bright})`;
}

function paintRecipeCard(card) {
  const canvas = card.querySelector('.recipe-card-canvas');
  const stats = card.querySelector('.recipe-energy');
  const fontsReady = document.fonts.check('16px "GCM Recipe"')
    && (!stats?.querySelector('.mc-bold') || document.fonts.check('700 16px "GCM Recipe"'));
  card.classList.toggle('recipe-card-painted', fontsReady);
  if (!canvas || !fontsReady) return;
  const ctx = canvas.getContext('2d');
  if (!ctx) { card.classList.remove('recipe-card-painted'); return; }
  const rect = card.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(card.clientWidth * dpr);
  canvas.height = Math.round(card.clientHeight * dpr);
  ctx.scale(dpr, dpr);
  const style = getComputedStyle(card);
  ctx.fillStyle = style.getPropertyValue('--recipe-canvas').trim();
  ctx.fillRect(0, 0, card.clientWidth, card.clientHeight);
  for (const slot of card.querySelectorAll('.recipe-crafting-slot')) {
    const r = slot.getBoundingClientRect(), x = Math.round(r.x-rect.x), y = Math.round(r.y-rect.y);
    const w = Math.round(r.width), h = Math.round(r.height), edge = Math.max(1,Math.round(w/Number(slot.dataset.w)));
    ctx.fillStyle = style.getPropertyValue('--recipe-slot').trim(); ctx.fillRect(x,y,w,h);
    ctx.fillStyle = style.getPropertyValue('--recipe-slot-shadow').trim();
    ctx.fillRect(x,y,w,edge); ctx.fillRect(x,y,edge,h);
    ctx.fillStyle = style.getPropertyValue('--recipe-frame-light').trim();
    ctx.fillRect(x,y+h-edge,w,edge); ctx.fillRect(x+w-edge,y,edge,h);
  }
  const arrow = card.querySelector('.recipe-crafting-arrow');
  if (arrow) {
    const r = arrow.getBoundingClientRect(), x = r.x-rect.x, y = r.y-rect.y;
    ctx.fillStyle = style.getPropertyValue('--recipe-frame-mid').trim();
    ctx.beginPath(); ctx.moveTo(x,y+r.height*.32); ctx.lineTo(x+r.width*.6,y+r.height*.32);
    ctx.lineTo(x+r.width*.6,y); ctx.lineTo(x+r.width,y+r.height/2);
    ctx.lineTo(x+r.width*.6,y+r.height); ctx.lineTo(x+r.width*.6,y+r.height*.68);
    ctx.lineTo(x,y+r.height*.68); ctx.closePath(); ctx.fill();
  }
  if (!stats) return;
  const size = parseFloat(getComputedStyle(stats).fontSize), scale = size / 8;
  const ink = style.getPropertyValue('--recipe-canvas-ink').trim();
  ctx.textBaseline = 'alphabetic';
  // Painting text onto the same opaque paper as the artwork prevents browser
  // automatic darkening from recolouring HTML labels independently of images.
  // The original text stays in the DOM for accessibility and copying.
  for (const line of stats.querySelectorAll('.recipe-card-line:not(:has(.recipe-card-fragment)), .recipe-card-fragment')) {
    const bounds = line.getBoundingClientRect();
    let x = Math.round(bounds.left - rect.left);
    const y = Math.round(bounds.top - rect.top + size);
    const walker = document.createTreeWalker(line, NodeFilter.SHOW_TEXT);
    for (let text = walker.nextNode(); text; text = walker.nextNode()) {
      let code = null, bold = false, underline = false;
      for (let el = text.parentElement; el && el !== line; el = el.parentElement) {
        code ??= [...el.classList].find(name => /^mc-[0-9a-f]$/.test(name))?.slice(-1);
        bold ||= el.classList.contains('mc-bold');
        underline ||= el.classList.contains('mc-underline');
      }
      ctx.font = `${bold ? 700 : 400} ${size}px "GCM Recipe"`;
      if (code === 'f') {
        ctx.fillStyle = style.getPropertyValue('--recipe-count-shadow').trim();
        ctx.fillText(text.textContent, x + scale, y + scale);
      }
      ctx.fillStyle = code ? gameColour(code) : ink;
      ctx.fillText(text.textContent, x, y);
      const advance = ctx.measureText(text.textContent).width;
      if (underline) ctx.fillRect(x, y + scale, advance, scale);
      x += advance;
    }
  }
}

export function voltageHtml(eut, tier) {
  const label = TIER_STYLE[tier]
    ? `<span class="recipe-voltage-tier ${TIER_STYLE[tier]}">${escapeHtml(tier)}</span>`
    : escapeHtml(tier || '');
  return `${integerText(eut)} EU/t${label ? ` (${label})` : ''}`;
}

export function energyHtml(step) {
  const lines = [];
  const r = step.scan;
  if (r) {
    if (r.max_eu != null) {
      const value = energyText(r.max_eu);
      lines.push(`<span title="${integerText(r.max_eu)} EU">Max EU: ${value} EU</span>`);
    }
    if (r.total_eu != null) lines.push(`Total: ${energyText(r.total_eu)} EU`);
    if (r.usage != null) lines.push(`Usage: ${voltageHtml(r.usage, r.method === 'scanner' ? r.tier : null)}`);
    if (r.method === 'research_station' && r.amperage > 1) lines.push(`Voltage: ${voltageHtml(r.eut, r.tier)} at ${integerText(r.amperage)} A`);
    if (r.method === 'scanner' && r.ticks != null) lines.push(`Time: ${Number(r.ticks / 20).toLocaleString('en-US')} seconds`);
    if (r.computation != null) lines.push(`Computation: ${integerText(r.computation)}`);
    if (r.computation_per_second != null) lines.push(`Min Computation: ${integerText(r.computation_per_second)} /s`);
  } else {
    if (step.eut > 0 && step.ticks > 0) {
      const total = BigInt(step.eut) * BigInt(step.ticks);
      lines.push(`<span title="${integerText(total)} EU">Total: ${energyText(total)} EU</span>`);
    }
    if (step.eut != null) lines.push(`Usage: ${voltageHtml(step.eut, step.tier)}`);
    if (step.ticks != null) lines.push(`Time: ${Number(step.ticks / 20).toLocaleString('en-US')} seconds`);
  }
  if (step.special) lines.push(step.heat ? `Heat: ${Number(step.special).toLocaleString('en-US')} K` : `Special: ${step.special}`);
  if (step.breeding) lines.push(`Chance: ${escapeHtml(Number(step.breeding.chance).toLocaleString('en-US'))}%`);
  const notes = [...(step.breeding?.conditions || []), ...(step.requirement_notes || [])].filter(note => {
    if (/^Cleanroom: (?:No|False)$/i.test(note)) return false; // Older cached pages.
    if (step.preview?.research && /^Research:/i.test(note)) return false;
    if (step.preview?.family === 'infusion' && /^Instability:/i.test(note)) return false;
    return !(step.preview?.aspects?.length && /^Essentia:/i.test(note));
  });
  const requirements = [...new Set(notes)].map(note => {
    const text = /^Cleanroom: (?:Yes|True)$/i.test(note) ? 'Needs Cleanroom' : note;
    return `<div class="recipe-card-line recipe-card-requirement" data-text="${escapeHtml(text)}">${escapeHtml(text)}</div>`;
  }).join('');
  return lines.length || requirements ? `<div class="recipe-energy">${lines.map(line => `<div class="recipe-card-line">${line}</div>`).join('')}${requirements}</div>` : '';
}

function researchWires(step) {
  const id = `research-core-${String(step.id).replace(/[^a-z0-9-]/gi, '')}`;
  const paths = [];
  for (let i = 0; i < 8; i++) {
    const x = 16 + 2 * i, y = 14 + 2 * i;
    paths.push(`M${x},8 V${y} H${40+i} V30`, `M${124-x},8 V${y} H${84-i} V30`,
      `M${x},76 V${84-y} H${40+i} V54`, `M${124-x},76 V${84-y} H${84-i} V54`,
      `M${40+2*i},8V20`, `M${66+2*i},8V20`, `M${40+2*i},66V76`, `M${66+2*i},66V76`);
  }
  return `<svg class="recipe-preview-wires" viewBox="0 0 170 82" aria-hidden="true">
    <defs><pattern id="${id}" width="2" height="2" patternUnits="userSpaceOnUse"><rect width="2" height="2" fill="#aaa"/><path d="M0,0H2 M0,0V2" stroke="#666" stroke-width=".5"/></pattern></defs>
    <path d="${paths.join(' ')}" fill="none" stroke="#fff" stroke-width="2"/>
    <path d="${paths.join(' ')}" fill="none" stroke="#111" stroke-width="1.1"/>
    <path d="M84,39H106 M84,42H106 M84,45H106 M115,51V58H133V62" fill="none" stroke="#fff" stroke-width="1.5"/>
    <rect x="38" y="20" width="48" height="46" fill="url(#${id})" stroke="#555" stroke-width="1"/>
  </svg>`;
}

export function amountText(e) {
  if (e.size == null) return '?';
  const n = Number(e.size).toLocaleString('en-US');
  if (e.kind === 'fluid') return `${n} mB`;
  if (e.size === 0) return 'not used up';
  return `${n}x`;
}

function previewAmountText(e) {
  if (e.size == null) return '?';
  if (e.size === 0) return 'NC';
  if (e.kind === 'fluid') {
    // Share the site's quantity units; three significant digits keep slots legible.
    const compact = formatQty(Number(e.size)).replace(/\d+\.\d+(?=[a-z])/i,
      n => String(Number(Number(n).toPrecision(3))));
    return `${compact}L`;
  }
  const n = Number(e.size);
  return n === 1 ? '' : n >= 10000 ? formatQty(n).replace(/\d+\.\d+(?=[a-z])/i,
    value => String(Number(Number(value).toPrecision(3)))) : n.toLocaleString('en-US');
}

export function stackHtml(e, options = {}) {
  const name = e.name || '?';
  const chance = e.chance ? ` <span class="recipe-chance">${escapeHtml(String(e.chance))}%</span>` : '';
  const icon = e.icon
    ? `<img class="${iconClass('recipe-stack-icon', e.icon)}" src="${iconUrl(e.icon)}" alt="" ${options.eager ? 'decoding="sync"' : 'loading="lazy"'} data-remove-on-error>`
    : options.interactive ? `<span class="recipe-stack-placeholder" aria-hidden="true">${escapeHtml(name.charAt(0))}</span>` : '';
  const nc = e.size === 0 && (options.preview || options.compact);
  const content = `${icon}<span class="recipe-stack-amount${nc ? ' recipe-stack-nc' : ''}">${escapeHtml(options.preview || options.compact ? previewAmountText(e) : amountText(e))}</span>`
    + `<span class="recipe-stack-name">${escapeHtml(name)}</span>${chance}`;
  if (!options.interactive || !e.key) return `<span class="recipe-stack" title="${escapeHtml(name)}">${content}</span>`;
  const key = escapeHtml(e.key);
  const tooltip = options.tooltipKey
    ? `data-recipe-tooltip="${escapeHtml(options.tooltipKey(e))}"${options.note ? ` data-tooltip-note="${escapeHtml(options.note)}"` : ''}`
    : `title="${escapeHtml(name)}${e.wildcard ? ' (any damage)' : ''}"`;
  return `<span class="recipe-stack-wrap${options.compact ? ' recipe-stack-compact' : ''}">
    <button class="recipe-stack recipe-stack-control${e.kind === 'fluid' ? ' recipe-stack-fluid' : ''}" data-action="recipe-open" data-key="${key}" ${tooltip} aria-label="Recipes for ${escapeHtml(name)}">${content}</button>
  </span>`;
}

export function machineText(step) {
  const [first, ...rest] = step.machines || [];
  if (!first) return step.map;
  const name = typeof first === 'string' ? first : first.name;
  return rest.length ? `${name} (or ${rest.length} more)` : name;
}

export function machineSidebarHtml(machines, options = {}) {
  const seen = new Set();
  const unique = machines.filter(machine => {
    const id = machine.key || machine.name;
    if (seen.has(id)) return false;
    seen.add(id); return true;
  });
  if (!unique.length) return '';
  const cells = unique.map(machine => {
    const name = machine.name || '?';
    const tooltip = options.tooltipKey
      ? `data-recipe-tooltip="${escapeHtml(options.tooltipKey({...machine, machine:true}))}"`
      : `title="${escapeHtml(name)}"`;
    const fallback = name.split(/\s+/).slice(0, 2).map(word => word[0] || '?').join('');
    const content = `<span class="recipe-machine-fallback" aria-hidden="true">${escapeHtml(fallback)}</span>${machine.icon
      ? `<img class="${iconClass('recipe-machine-icon', machine.icon)}" src="${iconUrl(machine.icon)}" alt="" loading="lazy" data-remove-on-error>` : ''}`;
    const id = `data-id="machine-${escapeHtml(machine.key || name)}"`;
    return machine.key
      ? `<li ${id}><button class="recipe-machine-control" ${tooltip} data-action="recipe-open" data-key="${escapeHtml(machine.key)}" aria-label="Recipes for ${escapeHtml(name)}">${content}</button></li>`
      : `<li ${id}><span class="recipe-machine-control recipe-machine-unknown" ${tooltip} tabindex="0" role="img" aria-label="${escapeHtml(name)}">${content}</span></li>`;
  }).join('');
  return `<aside class="recipe-machine-sidebar${unique.length > 1 ? ' recipe-machine-multiple' : ''}" aria-label="Recipe machines"><ul class="recipe-machine-grid">${cells}</ul></aside>`;
}

export function gridHtml(step, options = {}) {
  const grid = step.grid;
  const slots = grid.slots.map((slot, index) => {
    if (!slot) return `<span class="recipe-grid-hole" data-id="slot-${index}" aria-hidden="true"></span>`;
    const count = slot.alternatives_total;
    const note = [slot.ore, count > 1 ? `${count} alternatives${count > slot.alternatives.length ? ` (showing ${slot.alternatives.length})` : ''}` : ''].filter(Boolean).join(' · ');
    return `<span class="recipe-choice" data-id="slot-${index}" data-choice="${step.id}:${index}" ${options.tooltipKey ? '' : `title="${escapeHtml(note)}"`}>${stackHtml(slot.alternatives[0], { ...options, compact: true, note })}</span>`;
  });
  return `<div class="recipe-grid-group"><div class="recipe-grid recipe-grid-w${grid.w}">${slots.join('')}</div>
    <div class="recipe-grid-note">${grid.shapeless ? 'Shapeless · any order' : `${grid.w} × ${grid.h}${grid.mirrored ? ' · may be mirrored' : ''}`}</div></div>`;
}

export function displayChoices(alternatives) {
  return alternatives.flatMap(e => e.display_variants?.length ? e.display_variants : [e]).slice(0, 16);
}

export function craftingTitle(step) {
  return step.grid?.shapeless ? 'Shapeless Crafting' : 'Shaped Crafting';
}

export function craftingHtml(step, options = {}) {
  const grid = step.grid, cols = Math.max(3,grid.w), rows = Math.max(3,grid.h);
  const slots = [];
  for (let y=0; y<rows; y++) for (let x=0; x<cols; x++) {
    const index = y*grid.w+x;
    const input = x<grid.w && y<grid.h ? grid.slots[index] : null;
    const count = input?.alternatives_total || 0;
    const note = [input?.ore, count>1 ? `${count} alternatives${count>input.alternatives.length ? ` (showing ${input.alternatives.length})` : ''}` : ''].filter(Boolean).join(' · ');
    slots.push({x:25+x*18,y:12+y*18,w:18,h:18,alternatives:input?.alternatives || [],
      crafting:true,choice:input ? String(index) : `empty-${y}-${x}`,note});
  }
  for (const [index,output] of step.outputs.entries()) slots.push({x:65+cols*18+index*28,y:12+rows*9-13,w:26,h:26,
    alternatives:[output],crafting:true,output:true,choice:`output-${index}`});
  const preview = {family:'crafting',width:Math.max(166,112+cols*18+(step.outputs.length-1)*28),height:Math.max(92,24+rows*18),
    slots,layers:[],aspects:[]};
  return `<div class="recipe-crafting recipe-grid recipe-grid-w${grid.w}" role="group" aria-label="${craftingTitle(step)}">${previewHtml({...step,preview}, options)}</div>`;
}

export function previewHtml(step, options = {}) {
  const p = step.preview;
  const layers = p.layers.map(layer => {
    const [u, v, right, bottom] = layer.uv;
    return `<svg x="${layer.x}" y="${layer.y}" width="${layer.w}" height="${layer.h}" opacity="${layer.opacity ?? 1}" viewBox="${u} ${v} ${right-u} ${bottom-v}" preserveAspectRatio="none"><image href="${iconUrl(layer.image)}" width="1" height="1" preserveAspectRatio="none"/></svg>`;
  }).join('');
  // A breeding tree's (BBAB) lines from each bee to its parents, under the slots.
  const treeLines = (p.lines || []).map(l => `<line class="recipe-tree-line" x1="${l.x1}" y1="${l.y1}" x2="${l.x2}" y2="${l.y2}"/>`).join('');
  const treeLabels = (p.labels || []).map((label, i) => {
    const title = label.conditions?.length ? ` title="${escapeHtml(label.conditions.join('\n'))}"` : '';
    return `<span class="recipe-preview-slot recipe-tree-chance" data-id="tree-chance-${i}" data-x="${label.x}" data-y="${label.y}" data-w="${label.w}" data-h="${label.h}"${title}>${escapeHtml(label.text)}</span>`;
  }).join('');
  const slots = p.slots.map((slot, i) => {
    const e = displayChoices(slot.alternatives)[0];
    return `<span class="recipe-preview-slot${slot.crafting ? ' recipe-crafting-slot' : ''}${slot.output ? ' recipe-outputs' : ''}${slot.framed && !slot.frame ? ' recipe-preview-fallback-frame' : ''}" data-id="preview-${i}" data-choice="${step.id}:${slot.choice ?? `preview-${i}`}" data-x="${slot.x}" data-y="${slot.y}" data-w="${slot.w}" data-h="${slot.h}">${slot.frame ? `<img class="recipe-preview-frame" src="${iconUrl(slot.frame)}" alt="">` : ''}<span class="recipe-preview-choice">${e ? stackHtml(e, {...options, compact:true, preview:true,note:slot.note ?? options.note}) : ''}</span></span>`;
  }).join('');
  const research = p.research;
  const aspectTooltip = a => options.tooltipKey
    ? `data-recipe-tooltip="${escapeHtml(options.tooltipKey({kind:p.family === 'arcane' ? 'vis' : 'essentia', internal:a.tag, name:a.name, size:a.amount, mod:null,
      vis:p.family === 'arcane', info:{mod_name:'Thaumcraft'}}))}" tabindex="0" aria-label="${escapeHtml(a.name)}"`
    : `title="${escapeHtml(a.name)}${p.family === 'arcane' ? `: ${a.amount} vis` : ''}"`;
  const aspects = p.aspects.map(a => `<span class="recipe-preview-aspect" ${aspectTooltip(a)}>${a.icon ? `<img src="${iconUrl(a.icon)}" alt="${escapeHtml(a.name)}">` : escapeHtml(a.name)}<span>${a.amount}</span></span>`).join('');
  const arcane = p.family === 'arcane';
  const positionedAspects = arcane ? p.aspects.map(a => `<span class="recipe-preview-slot recipe-preview-aspect recipe-stack-compact" data-id="aspect-${escapeHtml(a.tag)}" data-x="${a.x}" data-y="${a.y}" data-w="${a.w}" data-h="${a.h}" ${aspectTooltip(a)}>${a.icon ? `<img src="${iconUrl(a.icon)}" alt="${escapeHtml(a.name)}">` : escapeHtml(a.name)}<span class="recipe-stack-amount">${a.amount}</span></span>`).join('') : '';
  return `${p.family === 'infusion' ? '<div class="recipe-preview-paper">' : ''}<div class="recipe-preview${p.family === 'infusion' ? ' recipe-preview-infusion' : ''}" data-width="${p.width}" data-height="${p.height}"><svg class="recipe-preview-layers" viewBox="0 0 ${p.width} ${p.height}" aria-hidden="true">${layers}${treeLines}</svg>${p.family === 'research_station' ? researchWires(step) : ''}${slots}${treeLabels}${positionedAspects}${p.family === 'crafting' ? `<span class="recipe-preview-slot recipe-crafting-arrow" data-x="${33+Math.max(3,step.grid.w)*18}" data-y="${6+Math.max(3,step.grid.h)*9}" data-w="26" data-h="12" aria-hidden="true"></span>` : ''}</div>
    ${p.family === 'infusion' ? `<div class="recipe-preview-instability">Instability: ${escapeHtml(String(step.requirements?.instability ?? '?'))}</div>` : ''}
    ${aspects && !arcane ? `<div class="recipe-preview-aspects">${aspects}</div>` : ''}
    ${p.family === 'infusion' ? '</div>' : ''}
    ${research ? `<div class="recipe-preview-research">${research.icon ? `<img src="${iconUrl(research.icon)}" alt="">` : ''}<span>Research: ${escapeHtml(research.name)}${research.category && !arcane ? ` · ${escapeHtml(research.category)}` : ''}</span></div>` : ''}`;
}

export function layoutRecipePreviews(root) {
  // Advances from the pinned font, after aligning it to an eight-pixel em.
  const narrow = {' ': .5, '!': .25, '"': .5, "'": .25, '(': .5, ')': .5, '*': .5,
    ',': .25, '.': .25, ':': .25, ';': .25, '<': .625, '>': .625, '@': .875,
    I: .5, '[': .5, ']': .5, '`': .375, f: .625, i: .25, k: .625, l: .375,
    t: .5, '{': .5, '|': .25, '}': .5, '~': .875};
  const textWidth = text => [...text].reduce((sum, char) => sum + (narrow[char] ?? .75), 0);
  for (const stats of root.querySelectorAll('.recipe-card .recipe-energy')) {
    const lines = [...stats.querySelectorAll('.recipe-card-line:not(.recipe-card-requirement)')];
    const ems = Math.max(1, ...lines.map(line => textWidth(line.textContent)
      + [...line.querySelectorAll('.mc-bold')].reduce((sum, bold) => sum + bold.textContent.length * .125, 0)));
    const width = stats.clientWidth;
    // One integer pixel scale per card keeps every line equally crisp.
    const scale = Math.max(1, Math.min(3, Math.round(width / 170), Math.floor(width / (ems * 8))));
    stats.style.fontSize = `${scale * 8}px`;
    stats.style.lineHeight = `${scale * 9}px`;
    for (const line of lines) line.style.fontSize = '';
    for (const line of stats.querySelectorAll('.recipe-card-requirement')) {
      // Long captured conditions wrap at the same readable pixel scale as
      // the costs, without shrinking every line to fit a metadata paragraph.
      const signature = `${width}:${scale}`;
      if (line.dataset.wrapSize === signature) continue;
      line.dataset.wrapSize = signature;
      const fragments = [];
      let fragment = '';
      for (const word of line.dataset.text.split(/\s+/)) {
        if (fragment && textWidth(`${fragment} ${word}`) * scale * 8 > width) {
          fragments.push(fragment); fragment = '';
        }
        for (const char of (fragment ? ' ' : '') + word) {
          if (fragment && textWidth(fragment + char) * scale * 8 > width) {
            fragments.push(fragment); fragment = '';
          }
          fragment += char;
        }
      }
      if (fragment) fragments.push(fragment);
      updateHtml(line, fragments.map(text => `<span class="recipe-card-fragment">${escapeHtml(text)}</span>`).join('\n'));
    }
  }

  for (const scene of root.querySelectorAll('.recipe-preview')) {
    const width = Number(scene.dataset.width), height = Number(scene.dataset.height);
    scene.style.aspectRatio = `${width} / ${height}`;
    // A whole number of device pixels per GUI pixel, the most that fits,
    // starting on a whole pixel. Pixel art stretched by a fraction has
    // pixels that could round either way, and Chrome rounds them
    // differently as it redraws: slot rims and corners shimmered.
    const dpr = window.devicePixelRatio || 1, parent = scene.parentElement;
    const css = getComputedStyle(parent), outer = parent.getBoundingClientRect();
    const contentLeft = outer.left + parseFloat(css.borderLeftWidth) + parseFloat(css.paddingLeft);
    const max = parseFloat(getComputedStyle(scene).maxWidth);
    const content = parent.clientWidth - parseFloat(css.paddingLeft) - parseFloat(css.paddingRight);
    const perPixel = Math.floor(Math.min(content, max || Infinity) * dpr / width) / dpr;
    if (perPixel > 0) {
      const sceneWidth = perPixel * width;
      scene.style.width = `${sceneWidth}px`;
      scene.style.marginLeft = `${Math.round((contentLeft + (content - sceneWidth) / 2) * dpr) / dpr - contentLeft}px`;
      scene.style.marginRight = 'auto';
    } else {
      scene.style.width = scene.style.marginLeft = scene.style.marginRight = '';
    }
    // Slot edges land on whole device pixels, so a slot's frame and icon
    // layers (app.css) do too. Rounding both edges (not the size) keeps
    // neighbouring slots on shared lines.
    const box = scene.getBoundingClientRect();
    const unit = box.width / width;
    const snap = (origin, at) => Math.round((origin + at * unit) * dpr) / dpr - origin;
    for (const slot of scene.querySelectorAll('.recipe-preview-slot')) {
      const x = Number(slot.dataset.x), y = Number(slot.dataset.y);
      const left = snap(box.left, x), top = snap(box.top, y);
      slot.style.left = `${left}px`;
      slot.style.top = `${top}px`;
      const slotWidth = snap(box.left, x + Number(slot.dataset.w)) - left;
      slot.style.width = `${slotWidth}px`;
      slot.style.height = `${snap(box.top, y + Number(slot.dataset.h)) - top}px`;
      // The icon's box (app.css's 5.56% inset, a frame texel) on whole
      // device pixels, rounded inwards: the icon is a layer of its own, and
      // one ending part way across a pixel by the rim makes the rim's
      // corner pixels flicker as it redraws.
      const inset = `${Math.ceil(slotWidth * dpr / 18) / dpr}px`;
      for (const control of slot.querySelectorAll('.recipe-stack-control')) control.style.padding = inset;
      // Use the same pixel scale as the artwork; only unusually long
      // quantities need smaller lettering to fit their own slot.
      for (const count of slot.querySelectorAll('.recipe-stack-control .recipe-stack-amount')) {
        const ems = textWidth(count.textContent);
        const scale = Math.max(1, Math.min(3, Math.round(scene.clientWidth / width)));
        const fit = slot.clientWidth / Math.max(1, ems);
        count.style.fontSize = ems ? `${Math.min(scale * 8, Math.floor(fit))}px` : '';
      }
    }
  }
  for (const card of root.querySelectorAll('.recipe-card')) paintRecipeCard(card);
}

export function stepHtml(step, options = {}) {
  const details = [!options.interactive ? step.tier : '',
    !options.interactive && step.heat && step.special ? `${Number(step.special).toLocaleString('en-US')} K` : ''].filter(Boolean).join(' · ');
  const machine = options.interactive
    ? `<button class="recipe-category-link" data-action="recipe-filter" data-map="${escapeHtml(step.map)}">${escapeHtml(machineText(step))}</button>`
    : escapeHtml(machineText(step));
  const groups = (step.groups || []).map((slot, index) => `<span class="recipe-choice" data-id="group-${index}" data-choice="${step.id}:group-${index}" ${options.tooltipKey ? '' : `title="${slot.alternatives_total} alternatives"`}>${stackHtml(slot.alternatives[0], { ...options, compact: true, note: `${slot.alternatives_total} alternatives` })}</span>`).join('');
  const inputs = step.grid ? gridHtml(step, options) : `<div class="recipe-side">${groups}${step.inputs.map(e => stackHtml(e, options)).join('')}</div>`;
  const graphic = options.interactive && step.grid ? craftingHtml(step, options)
    : options.interactive && step.preview ? previewHtml(step, options) : `<div class="recipe-step-sides">${inputs}<div class="recipe-arrow" aria-hidden="true">&rarr;</div>
      <div class="recipe-side recipe-outputs">${step.outputs.map(e => stackHtml(e, { ...options, compact: !!step.grid })).join('')}</div></div>`;
  const stats = options.interactive ? energyHtml(step) : '';
  const paper = options.interactive;
  return `<div class="recipe-step">
    ${options.machineSidebar ? details ? `<div class="recipe-step-details">${escapeHtml(details)}</div>` : '' : `<div class="recipe-step-machine">${machine}<span class="recipe-step-details">${escapeHtml(details)}</span></div>`}
    ${paper ? `<div class="recipe-card"><canvas class="recipe-card-canvas" aria-hidden="true"></canvas>${graphic}${stats}</div>` : graphic + stats}
    ${step.type === 'research' ? '<div class="recipe-grid-note">Scan the research item to write this Assembly Line recipe to a Data Stick.</div>' : ''}
  </div>`;
}
