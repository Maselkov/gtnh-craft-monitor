// NEI wheel regions follow geometry, so empty space in the tab band works too.
export function setupRecipeWheel(book, {isOpen, page, category}) {
  let region = null, direction = 0, pixels = 0, lastEvent = -Infinity;
  book.addEventListener('wheel', event => {
    if (!isOpen() || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey
        || !event.deltaY || Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;
    // These are independently scrollable lists, rather than recipe navigation.
    if (event.target.closest('.recipe-machine-sidebar, .recipe-book-information')) return;
    const nextRegion = event.clientY < book.querySelector('.recipe-book-pagination').getBoundingClientRect().top
      ? 'category' : 'page';
    const nextDirection = Math.sign(event.deltaY), now = event.timeStamp;
    if (region !== nextRegion || direction !== nextDirection || now-lastEvent > 180) {
      pixels = 0;
    }
    region = nextRegion; direction = nextDirection; lastEvent = now;
    event.preventDefault();
    event.stopPropagation();
    // Count every notch instead of discarding fast events. Browsers may also
    // combine several notches into a larger delta, so preserve that distance.
    const amount = Math.abs(event.deltaY);
    let steps;
    if (event.deltaMode !== 0 || amount >= 40) {
      steps = Math.max(1, Math.round(amount / (event.deltaMode === 1 ? 3 : event.deltaMode === 2 ? 1 : 100)));
      pixels = 0;
    } else {
      // Fine trackpad movement still needs a meaningful travel distance.
      pixels += amount;
      steps = Math.floor(pixels / 40);
      pixels %= 40;
    }
    if (!steps) return;
    if (region === 'category') category(direction * steps);
    else page(direction * steps);
  }, {passive:false});
}
