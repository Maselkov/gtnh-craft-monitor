import test from 'node:test';
import assert from 'node:assert/strict';
import { createRecipeData } from '../../static/js/recipe-data.js';

const meta = {version:'2.9.0',fingerprint:'one',assets:'textures'};
const frame = {key:'fluid:water',dir:'using',map:'mixer'};
const tick = () => new Promise(resolve => setImmediate(resolve));

test('metadata is shared across category selections, expires and retries failures', async () => {
  let time=0, calls=0, fail=false;
  const data = createRecipeData(async () => { calls++; if(fail)throw Error('offline'); return meta; },{now:()=>time});
  assert.strictEqual(await data.item(frame.key),meta);
  await data.item(frame.key); assert.equal(calls,1);
  time=30001; fail=true;
  await assert.rejects(data.item(frame.key),/offline/);
  fail=false; await data.item(frame.key); assert.equal(calls,3);
  data.clear(); await data.item(frame.key); assert.equal(calls,4);
});

test('preloads deduplicate with selected batches; background leaves room and foreground jumps the queue', async () => {
  const calls=[], releases=[];
  const data = createRecipeData(url => new Promise(resolve => {
    calls.push(new URL(url,'http://local').searchParams.get('map'));
    releases.push(()=>resolve({...meta,recipes:[]}));
  }));
  data.preload(meta,{...frame,map:'a'});
  data.preload(meta,{...frame,map:'b'});
  data.preload(meta,{...frame,map:'c'});
  await tick(); assert.deepEqual(calls,['a']);
  const selected=data.batch(meta,{...frame,map:'c'});
  await tick(); assert.deepEqual(calls,['a','c']);
  releases[1](); await selected;
  // Another selection of the same prefetched batch never issues a request.
  await data.batch(meta,{...frame,map:'c'}); assert.equal(calls.length,2);
  releases[0](); await tick(); assert.deepEqual(calls,['a','c','b']);
  releases[2](); await tick();
});

test('cancel queued preloads without poisoning future foreground loads; reject stale batches', async () => {
  const calls=[], releases=[];
  const data=createRecipeData(url=>new Promise(resolve=>{calls.push(url);releases.push(resolve);}));
  data.preload(meta,frame);
  data.preload(meta,{...frame,map:'other'});
  await tick(); data.cancelPreloads();
  const next=data.batch(meta,{...frame,map:'other'});
  await tick(); assert.equal(calls.length,2);
  releases[1]({...meta,fingerprint:'changed'});
  await assert.rejects(next,{code:'stale_recipes'});
  releases[0]({...meta,recipes:[]}); await tick();
  const retry=data.batch(meta,{...frame,map:'other'}); await tick();
  releases[2]({...meta,recipes:[]}); await retry;
});
