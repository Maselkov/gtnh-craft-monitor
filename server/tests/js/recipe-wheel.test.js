import test from 'node:test';
import assert from 'node:assert/strict';
import { setupRecipeWheel } from '../../static/js/recipe-wheel.js';

function fixture() {
  let handler, open = true, time = 0;
  const moves = [];
  const book = {addEventListener: (_name, callback) => { handler = callback; },
    querySelector: () => ({getBoundingClientRect: () => ({top:100})})};
  setupRecipeWheel(book, {isOpen:() => open, page:n => moves.push(['page',n]), category:n => moves.push(['category',n])});
  return {moves, close:() => { open = false; }, wheel: (options = {}) => {
    let prevented = false;
    handler({deltaX:0, deltaY:100, deltaMode:0, clientY:150, timeStamp:time += 200,
      target:{closest:() => null}, preventDefault:() => { prevented = true; }, stopPropagation() {}, ...options});
    return prevented;
  }};
}

test('wheel changes pages below the category heading and categories above it, including empty tab space', () => {
  const f = fixture();
  assert(f.wheel());
  assert(f.wheel({deltaY:-100}));
  assert(f.wheel({clientY:20}));
  assert(f.wheel({clientY:99, deltaY:-100}));
  assert.deepEqual(f.moves, [['page',1],['page',-1],['category',1],['category',-1]]);
});

test('wheel preserves nested scrolling, browser modifiers, horizontal gestures and closed dialogs', () => {
  const f = fixture();
  for (const options of [{ctrlKey:true},{metaKey:true},{altKey:true},{shiftKey:true},
    {deltaX:200},{deltaY:0},{target:{closest:() => ({})}}]) assert.equal(f.wheel(options), false);
  f.close(); assert.equal(f.wheel(), false);
  assert.deepEqual(f.moves, []);
});

test('fine trackpad movement accumulates while fast notches and reversals respond immediately', () => {
  const f = fixture();
  for (const timeStamp of [0,10,20]) f.wheel({deltaY:10,timeStamp});
  assert.deepEqual(f.moves, []);
  f.wheel({deltaY:10,timeStamp:30});
  f.wheel({deltaY:100,timeStamp:50});
  assert.deepEqual(f.moves, [['page',1],['page',1]]);
  f.wheel({deltaY:-100,timeStamp:60});
  f.wheel({deltaY:-100,timeStamp:170});
  assert.deepEqual(f.moves, [['page',1],['page',1],['page',-1],['page',-1]]);
  f.wheel({deltaY:1,deltaMode:1,timeStamp:400});
  f.wheel({deltaY:1,deltaMode:2,timeStamp:600});
  assert.deepEqual(f.moves.slice(-2), [['page',1],['page',1]]);
});

test('rapid wheel turns preserve every event and combined deltas can traverse several categories', () => {
  const f = fixture();
  for (let timeStamp=0; timeStamp<12; timeStamp++) f.wheel({clientY:20,timeStamp});
  assert.deepEqual(f.moves, Array.from({length:12}, () => ['category',1]));
  f.wheel({clientY:20,deltaY:600,timeStamp:12});
  f.wheel({clientY:20,deltaY:-1200,timeStamp:13});
  f.wheel({deltaY:300,timeStamp:14});
  assert.deepEqual(f.moves.slice(-3), [['category',6],['category',-12],['page',3]]);
});
