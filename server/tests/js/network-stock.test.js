import test from 'node:test';
import assert from 'node:assert/strict';
import { amountInSystem, ensureNetworkStock, setNetworkStock, setupNetworkStock } from '../../static/js/network-stock.js';

const part = {mod:'example',internal:'part',damage:0,kind:'item'};

test('unknown stock is distinct from a completed report with zero inventory', () => {
  setNetworkStock(null);
  assert.equal(amountInSystem(part), null);
  setNetworkStock({updated_at:null,items:[]});
  assert.equal(amountInSystem(part), null);
  setNetworkStock({updated_at:123,items:[]});
  assert.equal(amountInSystem(part), 0);
  assert.equal(amountInSystem({name:'Legacy item'}), null);
});

test('stock lookup respects variants, wildcard damage, fluids and plan storage keys', () => {
  setNetworkStock({updated_at:123,items:[
    {...part,size:11}, {...part,variant:'tag-a',size:7}, {...part,damage:1,size:3},
    {mod:null,internal:'example:fluid',kind:'fluid',damage:null,size:3000},
    {mod:null,internal:'aer',kind:'essentia',size:25},
    {...part,mod:'other',size:100},
  ]});
  assert.equal(amountInSystem(part), 11);
  assert.equal(amountInSystem({...part,variant:'tag-a'}), 7);
  assert.equal(amountInSystem(part,{aggregateVariants:true}), 18);
  assert.equal(amountInSystem({...part,wildcard:true,damage:null},{aggregateVariants:true}), 21);
  assert.equal(amountInSystem({key:'example|part|0|item|tag-a'}), 7);
  assert.equal(amountInSystem({mod:null,internal:'example:fluid',kind:'fluid'}), 3000);
  assert.equal(amountInSystem({mod:null,internal:'aer',kind:'essentia'}), 25);
  assert.equal(amountInSystem({...part,internal:'missing'}), 0);
});

test('stock counts follow new reports rather than a tooltip entry stale quantity', () => {
  const item = {...part,size:999};
  setNetworkStock({updated_at:123,items:[{...part,size:11}]});
  assert.equal(amountInSystem(item), 11);
  setNetworkStock({updated_at:124,items:[{...part,size:42}]});
  assert.equal(amountInSystem(item), 42);
  setNetworkStock(null);
  assert.equal(amountInSystem(item), null);
});

test('tooltips can request stock lazily without duplicating in-flight or fresh reports', async () => {
  setNetworkStock(null);
  let calls=0, release;
  setupNetworkStock(async () => {
    calls++;
    await new Promise(resolve => { release=resolve; });
    setNetworkStock({updated_at:123,items:[{...part,size:42}]});
  });
  try {
    const first=ensureNetworkStock(), second=ensureNetworkStock();
    assert.equal(first,second);
    await Promise.resolve(); assert.equal(calls,1);
    release(); await first;
    assert.equal(amountInSystem(part),42);
    await ensureNetworkStock(); assert.equal(calls,1);
  } finally { setupNetworkStock(null); setNetworkStock(null); }
});
