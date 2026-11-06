import test from 'node:test';
import assert from 'node:assert/strict';
import { connectedGroups, ctmContext, quadFrame, quarterTile } from '../../static/js/ctm.js';

// A quad as BlockMeshes.java captures it: four corners in 1/256 of a
// block, texture coordinates in 1/256 of its tile.
function quad(corners, uvs) {
  return { p: corners.flat(), uv: uvs.flat() };
}

// The top face, texture u along +x and v along +z; and the west and east
// faces, for leaving out faces against the same glass.
const TOP = quad([[0, 256, 0], [256, 256, 0], [256, 256, 256], [0, 256, 256]], [[0, 0], [256, 0], [256, 256], [0, 256]]);
const WEST = quad([[0, 0, 0], [0, 256, 0], [0, 256, 256], [0, 0, 256]], [[0, 256], [0, 0], [256, 0], [256, 256]]);
const EAST = quad([[256, 0, 256], [256, 256, 256], [256, 256, 0], [256, 0, 0]], [[0, 256], [0, 0], [256, 0], [256, 256]]);

function mesh(tile, ...quads) {
  return {
    t: quads.map(() => tile),
    p: quads.flatMap(q => q.p),
    uv: quads.flatMap(q => q.uv),
    c: quads.flatMap(() => [0xffffff, 0xffffff, 0xffffff, 0xffffff]),
  };
}

const GLASS = 'bartworks:BW_TieredGlass:0';
// Its five compact tiles are atlas tiles 10-14: 0 bordered, 1 seamless,
// 2 joined up/down, 3 joined left/right, 4 inner corner.
const DEFINITION = { tiles: [10, 11, 12, 13, 14], blocks: [GLASS], connect: 'block' };

function row(n, extra = {}) {
  const block = { key: GLASS, translucent: true, mesh: mesh(5, TOP, WEST, EAST), positions: [], ...extra };
  for (let x = 0; x < n; x++) block.positions.push(x, 0, 0);
  return block;
}

test('a quad knows its face and which way its texture runs', () => {
  const frame = quadFrame(mesh(5, TOP), 0);
  assert.equal(frame.face, 1);
  assert.deepEqual(frame.u, [1, 0, 0]);
  assert.deepEqual(frame.v, [0, 0, 1]);
  assert.equal(quadFrame(mesh(5, WEST), 0).face, 4);
  // Not a whole face: CTM leaves it alone.
  assert.equal(quadFrame(mesh(5, quad([[0, 256, 0], [128, 256, 0], [128, 256, 128], [0, 256, 128]],
    [[0, 0], [128, 0], [128, 128], [0, 128]])), 0), null);
});

test('each quarter takes its tile from its sides and diagonal', () => {
  const none = { L: false, R: false, U: false, D: false, UL: false, UR: false, DL: false, DR: false };
  assert.equal(quarterTile(none, 0), 0);
  assert.equal(quarterTile({ ...none, L: true }, 0), 3);
  assert.equal(quarterTile({ ...none, U: true }, 0), 2);
  assert.equal(quarterTile({ ...none, L: true, U: true }, 0), 4);
  assert.equal(quarterTile({ ...none, L: true, U: true, UL: true }, 0), 1);
  // The top-right quarter looks up and right, not left.
  assert.equal(quarterTile({ ...none, L: true }, 1), 0);
  assert.equal(quarterTile({ ...none, R: true }, 1), 3);
});

test('a row of glass joins up, and leaves out the faces between', () => {
  const block = row(3);
  const groups = connectedGroups(block, ctmContext({ blocks: [block], ctm: [DEFINITION] }));
  const at = (x) => groups.find(g => g.positions[0] === x || g.positions.includes(x));
  // The two ends differ from the middle and from each other.
  assert.equal(groups.length, 3);
  const middle = groups.find(g => g.positions.length === 3 && g.positions[0] === 1);
  // Top face joined left and right all along: one quad, tile 3. The
  // west and east faces are against glass and left out.
  assert.deepEqual(middle.mesh.t, [13]);
  const end = groups.find(g => g.positions[0] === 0);
  // Its top face split into quarters: the left half bordered (0), the
  // right half joined to the right (3). Its west face, out in the open,
  // stays (bordered, whole); its east face, against glass, goes.
  assert.deepEqual(end.mesh.t, [10, 13, 13, 10, 10]);
  assert.ok(at(2));
});

test('quarters keep their place on the face and their part of the tile', () => {
  const block = row(2);
  const [first] = connectedGroups(block, ctmContext({ blocks: [block], ctm: [DEFINITION] }))
    .filter(g => g.positions[0] === 0);
  // The top-right quarter: x from 128 to 256, z from 0 to 128, showing the
  // same part of tile 3.
  const q = 1;
  const corners = [0, 1, 2, 3].map(i => first.mesh.p.slice((q * 4 + i) * 3, (q * 4 + i) * 3 + 3));
  const uvs = [0, 1, 2, 3].map(i => first.mesh.uv.slice((q * 4 + i) * 2, (q * 4 + i) * 2 + 2));
  assert.deepEqual(corners, [[128, 256, 0], [256, 256, 0], [256, 256, 128], [128, 256, 128]]);
  assert.deepEqual(uvs, [[128, 0], [256, 0], [256, 128], [128, 128]]);
});

test('a block without a definition is drawn as it was captured, in one group', () => {
  const block = row(3, { key: 'gregtech:gt.blockcasings:0', translucent: false });
  const groups = connectedGroups(block, ctmContext({ blocks: [block], ctm: [DEFINITION] }));
  assert.equal(groups.length, 1);
  assert.equal(groups[0].mesh, block.mesh);
});

test('a definition by texture joins blocks drawn with it', () => {
  // Coils: matched by their texture (atlas tile 5), joined by block.
  const coil = row(2, { key: 'gregtech:gt.blockcasings5:0', translucent: false });
  const groups = connectedGroups(coil, ctmContext({
    blocks: [coil], ctm: [{ tiles: [20, 21, 22, 23, 24], sprites: [5], connect: 'block' }],
  }));
  // Opaque: no faces left out; each end's top face joins its neighbour.
  assert.equal(groups.length, 2);
  for (const g of groups) assert.equal(g.mesh.t.length, 4 + 2);
});
