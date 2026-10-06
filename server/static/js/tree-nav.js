// Moving about a tree that comes from the server a few levels at a
// time (planner.trim): the craft plan's tree (craft-plan.js) and a busy
// CPU's job tree (job-tree.js). A step is found by its child positions
// from the top ("0.3.1"); a step whose own steps weren't sent yet has
// `more` and no `children`.

export function nodeAt(root, positions) {
  let node = root;
  for (const p of positions === '' ? [] : positions.split('.')) {
    node = node && node.children ? node.children[Number(p)] : null;
  }
  return node;
}

// Fetches whatever the server left out on the way down to a step, with
// load(prefix) (resolving true once that step's steps are in); false if
// any of it couldn't be.
export async function ensureLoaded(root, positions, load) {
  const steps = positions === '' ? [] : positions.split('.');
  for (let depth = 0; depth <= steps.length; depth++) {
    const prefix = steps.slice(0, depth).join('.');
    const node = nodeAt(root, prefix);
    if (!node) return false;
    if (depth < steps.length && !node.children && node.more > 0) {
      if (!await load(prefix)) return false;
    }
  }
  return true;
}

// Marks every step above the one at positions open, by the paths each
// tree keeps its open/shut state under: segment(node, positions) per
// step, joined by ' > '.
export function openAbove(root, positions, openState, segment) {
  let node = root;
  let path = segment(node, '');
  let at = '';
  for (const p of positions === '' ? [] : positions.split('.')) {
    openState.set(path, true);
    node = node.children[Number(p)];
    at = at === '' ? p : `${at}.${p}`;
    path += ' > ' + segment(node, at);
  }
}

// Scrolls the drawn step at positions into the middle of its box and
// flashes it (.plan-jump).
export function flashRow(container, positions) {
  const row = container.querySelector(`li[data-pos="${CSS.escape(positions)}"] > .plan-row`);
  if (!row) return;
  row.scrollIntoView({ block: 'center' });
  row.classList.remove('plan-jump');
  void row.offsetWidth;  // restart the flash on a second jump to the same row
  row.classList.add('plan-jump');
}
