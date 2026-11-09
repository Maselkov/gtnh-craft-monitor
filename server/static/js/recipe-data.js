// Shared immutable recipe data, separate from each history frame's page/scroll.
import { sameRecipeDataset } from './recipe-state.js';

export function createRecipeData(get, {now = Date.now} = {}) {
  const metadata = new Map(), batches = new Map(), queue = [];
  let active = 0;
  const remember = (cache, key, value, max) => {
    cache.delete(key); cache.set(key, value);
    while (cache.size > max) cache.delete(cache.keys().next().value);
    return value;
  };
  function pump() {
    queue.sort((a,b) => Number(b.foreground)-Number(a.foreground));
    while (active < 2 && queue.length) {
      // Background work uses at most one slot, leaving room for navigation.
      if (!queue[0].foreground && active) return;
      const task = queue.shift(); active++;
      Promise.resolve().then(task.run).then(task.resolve, task.reject).finally(() => { active--; pump(); });
    }
  }
  function request(cache, key, url, foreground, max, check = value => value) {
    let record = cache.get(key);
    if (record) {
      if (foreground) { record.task.foreground = true; pump(); }
      return record.promise;
    }
    const task = {foreground, run:() => get(url).then(check)};
    record = {task, promise:new Promise((resolve,reject) => { task.resolve=resolve; task.reject=reject; })};
    record.promise.then(value => { record.value = value; }, () => {});
    task.evict = () => { if (cache.get(key) === record) cache.delete(key); };
    remember(cache,key,record,max);
    record.promise.catch(() => { if (cache.get(key) === record) cache.delete(key); });
    queue.push(task); pump();
    return record.promise;
  }
  function item(key) {
    const record = metadata.get(key);
    // Check the dataset periodically, rather than on every machine switch.
    if (record?.expires <= now()) metadata.delete(key);
    return request(metadata,key,'/api/recipes/item?'+new URLSearchParams({key}),true,50,data => {
      const current = metadata.get(key);
      if (current) current.expires = now()+30000;
      return data;
    });
  }
  function batchQuery(meta, frame, offset) {
    return new URLSearchParams({key:frame.key,dir:frame.dir,map:frame.map,offset:String(offset),limit:'20',
      version:meta.version,fingerprint:meta.fingerprint,assets:meta.assets || ''});
  }
  function batch(meta, frame, offset = 0, foreground = true) {
    const query = batchQuery(meta,frame,offset);
    return request(batches,query.toString(),'/api/recipes?'+query,foreground,100,data => {
      if (!sameRecipeDataset(meta,data)) throw Object.assign(new Error('Game data changed. Reload recipes to continue.'),{code:'stale_recipes'});
      return data;
    });
  }
  function cancelPreloads() {
    for (let i=queue.length-1;i>=0;i--) {
      if (queue[i].foreground) continue;
      const task = queue.splice(i,1)[0];
      task.evict(); task.reject(new DOMException('Navigation changed','AbortError'));
    }
  }
  return {item,batch,cancelPreloads,
    peekItem:key => { const record=metadata.get(key); return record?.expires > now() ? record.value : undefined; },
    peekBatch:(meta,frame,offset=0) => batches.get(batchQuery(meta,frame,offset).toString())?.value,
    preload:(meta,frame,offset=0) => { batch(meta,frame,offset,false).catch(() => {}); },
    clear:() => { cancelPreloads(); metadata.clear(); batches.clear(); }};
}
