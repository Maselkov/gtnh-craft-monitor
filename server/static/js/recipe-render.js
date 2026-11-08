// Shared recipe rendering. Pattern checks use static stacks; the viewer
// opts into navigation, stock links and grid slots. No setup on import.
import { escapeHtml, iconClass, iconUrl } from './util.js';

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
  const n = Number(e.size).toLocaleString('en-US');
  return e.kind === 'fluid' ? `${n} L` : e.size === 1 ? '' : n;
}

export function stackHtml(e, options = {}) {
  const name = e.name || '?';
  const chance = e.chance ? ` <span class="recipe-chance">${escapeHtml(String(e.chance))}%</span>` : '';
  const icon = e.icon
    ? `<img class="${iconClass('recipe-stack-icon', e.icon)}" src="${iconUrl(e.icon)}" alt="" loading="lazy" data-remove-on-error>`
    : options.interactive ? `<span class="recipe-stack-placeholder" aria-hidden="true">${escapeHtml(name.charAt(0))}</span>` : '';
  const content = `${icon}<span class="recipe-stack-amount">${escapeHtml(options.preview ? previewAmountText(e) : amountText(e))}</span>`
    + `<span class="recipe-stack-name">${escapeHtml(name)}</span>${chance}`;
  if (!options.interactive || !e.key) return `<span class="recipe-stack" title="${escapeHtml(name)}">${content}</span>`;
  const key = escapeHtml(e.key);
  const tooltip = options.tooltipKey
    ? `data-recipe-tooltip="${escapeHtml(options.tooltipKey(e))}"${options.note ? ` data-tooltip-note="${escapeHtml(options.note)}"` : ''}`
    : `title="${escapeHtml(name)}${e.wildcard ? ' (any damage)' : ''}"`;
  const stock = options.hasStock && options.hasStock(e)
    ? `<button class="recipe-stock-link" data-action="recipe-stock" data-key="${key}" aria-label="Open stock page for ${escapeHtml(name)}">Stock</button>` : '';
  return `<span class="recipe-stack-wrap${options.compact ? ' recipe-stack-compact' : ''}">
    <button class="recipe-stack recipe-stack-control" data-action="recipe-open" data-key="${key}" ${tooltip} aria-label="Recipes for ${escapeHtml(name)}">${content}</button>
    <span class="recipe-stack-links"><button data-action="recipe-open" data-dir="using" data-key="${key}" aria-label="Uses for ${escapeHtml(name)}">Uses</button>${stock}</span>
  </span>`;
}

export function machineText(step) {
  const [first, ...rest] = step.machines || [];
  if (!first) return step.map;
  const name = typeof first === 'string' ? first : first.name;
  return rest.length ? `${name} (or ${rest.length} more)` : name;
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

export function previewHtml(step, options = {}) {
  const p = step.preview;
  const layers = p.layers.map(layer => {
    const [u, v, right, bottom] = layer.uv;
    return `<svg x="${layer.x}" y="${layer.y}" width="${layer.w}" height="${layer.h}" viewBox="${u} ${v} ${right-u} ${bottom-v}" preserveAspectRatio="none"><image href="${iconUrl(layer.image)}" width="1" height="1" preserveAspectRatio="none"/></svg>`;
  }).join('');
  const slots = p.slots.map((slot, i) => {
    const e = displayChoices(slot.alternatives)[0];
    return `<span class="recipe-preview-slot${slot.framed && !slot.frame ? ' recipe-preview-fallback-frame' : ''}" data-id="preview-${i}" data-choice="${step.id}:preview-${i}" data-x="${slot.x}" data-y="${slot.y}" data-w="${slot.w}" data-h="${slot.h}">${slot.frame ? `<img class="recipe-preview-frame" src="${iconUrl(slot.frame)}" alt="">` : ''}<span class="recipe-preview-choice">${e ? stackHtml(e, {...options, compact:true, preview:true}) : ''}</span></span>`;
  }).join('');
  const research = p.research;
  const aspects = p.aspects.map(a => `<span class="recipe-preview-aspect" title="${escapeHtml(a.name)}">${a.icon ? `<img src="${iconUrl(a.icon)}" alt="${escapeHtml(a.name)}">` : escapeHtml(a.name)}<span>${a.amount}</span></span>`).join('');
  return `${p.family === 'infusion' ? '<div class="recipe-preview-paper">' : ''}<div class="recipe-preview${p.family === 'infusion' ? ' recipe-preview-infusion' : ''}" data-width="${p.width}" data-height="${p.height}"><svg class="recipe-preview-layers" viewBox="0 0 ${p.width} ${p.height}" aria-hidden="true">${layers}</svg>${slots}</div>
    ${p.family === 'infusion' ? `<div class="recipe-preview-instability">Instability: ${escapeHtml(String(step.requirements?.instability ?? '?'))}</div>` : ''}
    ${aspects ? `<div class="recipe-preview-aspects">${aspects}</div>` : ''}
    ${p.family === 'infusion' ? '</div>' : ''}
    ${research ? `<div class="recipe-preview-research">${research.icon ? `<img src="${iconUrl(research.icon)}" alt="">` : ''}<span>Research: ${escapeHtml(research.name)}${research.category ? ` · ${escapeHtml(research.category)}` : ''}</span></div>` : ''}`;
}

export function layoutRecipePreviews(root) {
  for (const scene of root.querySelectorAll('.recipe-preview')) {
    const width = Number(scene.dataset.width), height = Number(scene.dataset.height);
    scene.style.aspectRatio = `${width} / ${height}`;
    for (const slot of scene.querySelectorAll('.recipe-preview-slot')) {
      slot.style.left = `${Number(slot.dataset.x) / width * 100}%`;
      slot.style.top = `${Number(slot.dataset.y) / height * 100}%`;
      slot.style.width = `${Number(slot.dataset.w) / width * 100}%`;
      slot.style.height = `${Number(slot.dataset.h) / height * 100}%`;
    }
  }
}

export function stepHtml(step, options = {}) {
  const details = [step.tier, options.interactive && step.eut != null ? `${Number(step.eut).toLocaleString('en-US')} EU/t` : '',
    options.interactive && step.ticks != null ? `${step.ticks / 20} s` : '',
    options.interactive && step.eut > 0 && step.ticks > 0 ? `${(step.eut * step.ticks).toLocaleString('en-US')} total EU` : '',
    step.heat && step.special ? `${Number(step.special).toLocaleString('en-US')} K` : '',
    options.interactive && !step.heat && step.special ? `Special: ${step.special}` : ''].filter(Boolean).join(' · ');
  const machine = options.interactive
    ? `<button class="recipe-category-link" data-action="recipe-filter" data-map="${escapeHtml(step.map)}">${escapeHtml(machineText(step))}</button>`
    : escapeHtml(machineText(step));
  const groups = (step.groups || []).map((slot, index) => `<span class="recipe-choice" data-id="group-${index}" data-choice="${step.id}:group-${index}" ${options.tooltipKey ? '' : `title="${slot.alternatives_total} alternatives"`}>${stackHtml(slot.alternatives[0], { ...options, compact: true, note: `${slot.alternatives_total} alternatives` })}</span>`).join('');
  const inputs = step.grid ? gridHtml(step, options) : `<div class="recipe-side">${groups}${step.inputs.map(e => stackHtml(e, options)).join('')}</div>`;
  const research = step.research;
  const researchDetails = research ? [research.method === 'research_station' ? 'Research Station' : 'Scanner',
    research.method === 'scanner' && research.ticks ? `${research.ticks / 20} s` : '',
    research.eut ? `${Number(research.eut).toLocaleString('en-US')} EU/t` : '',
    research.computation ? `${Number(research.computation).toLocaleString('en-US')} total computation` : '',
    research.computation_per_second ? `${Number(research.computation_per_second).toLocaleString('en-US')} computation/s` : '',
    research.amperage ? `${research.amperage} A` : ''].filter(Boolean).join(' · ') : '';
  const requirements = step.requirement_notes?.length ? `<details class="recipe-requirements"><summary>Additional requirements</summary><ul>${step.requirement_notes.map(note => `<li>${escapeHtml(note)}</li>`).join('')}</ul></details>` : '';
  return `<div class="recipe-step">
    <div class="recipe-step-machine">${machine}<span class="recipe-step-details">${escapeHtml(details)}</span></div>
    ${options.interactive && step.preview ? previewHtml(step, options) : `<div class="recipe-step-sides">${inputs}<div class="recipe-arrow" aria-hidden="true">&rarr;</div>
      <div class="recipe-side recipe-outputs">${step.outputs.map(e => stackHtml(e, { ...options, compact: !!step.grid })).join('')}</div>
    </div>`}
    ${research ? `<div class="recipe-research"><span>Research required · ${escapeHtml(researchDetails)}</span>${stackHtml(research.item, options)}</div>` : ''}
    ${step.type === 'nei' ? '<div class="recipe-approximation">NEI display recipe · additional costs, catalysts or conditions may apply. Check NEI in game.</div>' : ''}
    ${requirements}
  </div>`;
}
