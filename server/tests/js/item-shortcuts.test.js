import test from 'node:test';
import assert from 'node:assert/strict';
import { recipeShortcutDirection } from '../../static/js/item-shortcuts.js';

test('shared item recipe keys allow Shift for details but ignore browser and typing keys', () => {
  assert.equal(recipeShortcutDirection({key:'r'}), 'making');
  assert.equal(recipeShortcutDirection({key:'U',shiftKey:true}), 'using');
  for (const option of ['defaultPrevented','repeat','isComposing','ctrlKey','metaKey','altKey']) {
    assert.equal(recipeShortcutDirection({key:'r',[option]:true}), null, option);
  }
  assert.equal(recipeShortcutDirection({key:'p'}), null);
  assert.equal(recipeShortcutDirection({key:'r',target:{isContentEditable:true}}), null);
  assert.equal(recipeShortcutDirection({key:'u',target:{closest: selector => selector.includes('input') ? {} : null}}), null);
});

test('crafting-plan storage keys resolve identity without guessing from labels or NBT', async () => {
  const {recipeItemKey} = await import('../../static/js/recipe-state.js');
  assert.equal(recipeItemKey({key:'gregtech|part|7|item|variant',kind:'item'}), 'gregtech:part:7');
  assert.equal(recipeItemKey({key:'|mod:water||fluid',kind:'fluid'}), 'fluid:mod:water');
  assert.equal(recipeItemKey({key:'|ordo||essentia',kind:'essentia'}), null);
  assert.equal(recipeItemKey({key:'|Iron Ingot||unknown',name:'Iron Ingot'}), null);
  assert.equal(recipeItemKey({key:'mod|part|bad|item'}), null);
});
