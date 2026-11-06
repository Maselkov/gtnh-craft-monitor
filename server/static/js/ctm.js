// Connected textures for the kit's 3D view: glass, coils and casings
// that join up with their neighbours, as MCPatcher's "compact" method
// draws them in the game (Angelica's CompactCtmQuadProcessor). The game
// data has the definitions (Layouts.java's ctm: which blocks or textures,
// how they connect, five tiles); this works out, for each block of a
// multiblock, which tile each quarter of each face shows. Pure functions
// (tested under server/tests/js/).
//
// A face of a block a definition applies to is split into quarters. Each
// quarter looks at the neighbours on its two sides and the diagonal
// between them, in the face's own texture directions (left is where the
// texture's u goes down, up where its v goes down - read from the quad as
// the game drew it, so it matches however the face was turned):
//   neither side connects        tile 0 (bordered)
//   only left or right           tile 3
//   only up or down              tile 2
//   both sides and the diagonal  tile 1 (seamless)
//   both sides, not the diagonal tile 4 (inner corner)
// A face whose quarters all take the same tile stays one quad.
//
// A see-through block (glass) also leaves out a face against the same
// block, as the game does, so a wall of glass isn't full of inner faces.

const OFFSET = 512;

function pack(x, y, z) {
  return ((x + OFFSET) * 1024 + (y + OFFSET)) * 1024 + (z + OFFSET);
}

// "mod:name:meta" -> "mod:name"; null for a GregTech machine (mte:...).
function blockName(key) {
  if (key.startsWith('mte:')) return null;
  return key.slice(0, key.lastIndexOf(':'));
}

// Which kinds of block are where, and what each definition applies to.
export function ctmContext(data) {
  const at = new Map();         // packed position -> block key
  const sprites = new Map();    // block key -> Set of atlas tiles its mesh uses
  for (const block of data.blocks) {
    const p = block.positions;
    for (let i = 0; i < p.length; i += 3) at.set(pack(p[i], p[i + 1], p[i + 2]), block.key);
    sprites.set(block.key, new Set(block.mesh ? block.mesh.t : []));
  }
  return { at, sprites, definitions: data.ctm || [] };
}

// The definition for one quad of a block, or null.
function definitionFor(ctx, blockKey, tile, face) {
  for (const d of ctx.definitions) {
    const matches = (d.blocks && d.blocks.includes(blockKey)) || (d.sprites && d.sprites.includes(tile));
    if (matches && (!d.faces || d.faces.includes(face))) return d;
  }
  return null;
}

const unit = (v) => v.map(c => Math.abs(c) < 1e-6 ? 0 : Math.sign(c));

// A quad's shape: its face (Minecraft's 0-5), the world directions its
// texture's u and v run in, and where its texture's (0, 0) is, so any
// point of the texture can be put back on the face. Null for a quad
// that isn't a whole face of the block (CTM only joins whole faces).
export function quadFrame(mesh, q) {
  const P = [], UV = [];
  for (let i = 0; i < 4; i++) {
    const v = q * 4 + i;
    P.push([mesh.p[3 * v], mesh.p[3 * v + 1], mesh.p[3 * v + 2]]);
    UV.push([mesh.uv[2 * v], mesh.uv[2 * v + 1]]);
  }
  const us = UV.map(t => t[0]), vs = UV.map(t => t[1]);
  if (Math.min(...us) !== 0 || Math.max(...us) !== 256 || Math.min(...vs) !== 0 || Math.max(...vs) !== 256) {
    return null;
  }
  // Solve [e1 e2] = [U V] [duv1 duv2] for U and V, per 1/256 of the texture.
  for (const [a, b] of [[1, 3], [1, 2], [2, 3]]) {
    const e1 = P[a].map((c, k) => c - P[0][k]), e2 = P[b].map((c, k) => c - P[0][k]);
    const du1 = UV[a][0] - UV[0][0], dv1 = UV[a][1] - UV[0][1];
    const du2 = UV[b][0] - UV[0][0], dv2 = UV[b][1] - UV[0][1];
    const det = du1 * dv2 - du2 * dv1;
    if (Math.abs(det) < 1e-9) continue;
    const U = [0, 1, 2].map(k => (e1[k] * dv2 - e2[k] * dv1) / det);
    const V = [0, 1, 2].map(k => (e2[k] * du1 - e1[k] * du2) / det);
    const n = [U[1] * V[2] - U[2] * V[1], U[2] * V[0] - U[0] * V[2], U[0] * V[1] - U[1] * V[0]];
    // The face is the side the quad is on, whichever way it's wound.
    const axis = [0, 1, 2].reduce((m, k) => Math.abs(n[k]) > Math.abs(n[m]) ? k : m, 0);
    const centre = P.reduce((s, p) => s + p[axis], 0) / 4;
    const positive = centre > 128;
    const face = [[4, 5], [0, 1], [2, 3]][axis][positive ? 1 : 0];
    const origin = [0, 1, 2].map(k => P[0][k] - UV[0][0] * U[k] - UV[0][1] * V[k]);
    return { face, normal: unit([0, 1, 2].map(k => k === axis ? (positive ? 1 : -1) : 0)),
      u: unit(U), v: unit(V), U, V, origin };
  }
  return null;
}

const QUARTERS = [
  // [u from, u to, v from, v to, its two sides and the diagonal]
  [0, 128, 0, 128, 'L', 'U', 'UL'],
  [128, 256, 0, 128, 'U', 'R', 'UR'],
  [128, 256, 128, 256, 'R', 'D', 'DR'],
  [0, 128, 128, 256, 'D', 'L', 'DL'],
];

// Which compact tile (0-4) a quarter takes, from what connects.
export function quarterTile(connects, quarter) {
  const [, , , , a, b, diagonal] = QUARTERS[quarter];
  const ca = connects[a], cb = connects[b];
  if (ca && cb) return connects[diagonal] ? 1 : 4;
  if (ca || cb) return (ca ? a : b) === 'L' || (ca ? a : b) === 'R' ? 3 : 2;
  return 0;
}

function connectsTo(ctx, definition, blockKey, tile, neighbour) {
  if (!neighbour) return false;
  if (definition.connect === 'tile') return ctx.sprites.get(neighbour)?.has(tile) ?? false;
  // By block: the same block, and one of the definition's own where it lists them.
  if (definition.blocks) return definition.blocks.includes(neighbour);
  return blockName(neighbour) !== null && blockName(neighbour) === blockName(blockKey);
}

// What each of a block's quads does at x, y, z: [] for one drawn as
// captured, null for one left out (a see-through face against the same
// block), or {definition, connects} for one that joins up.
function quadStates(ctx, block, frames, x, y, z) {
  const near = (d) => ctx.at.get(pack(x + d[0], y + d[1], z + d[2]));
  return frames.map((frame, q) => {
    if (!frame) return [];
    if (block.translucent && near(frame.normal) === block.key) return null;
    const definition = definitionFor(ctx, block.key, block.mesh.t[q], frame.face);
    if (!definition) return [];
    const left = frame.u.map(c => -c), right = frame.u, up = frame.v.map(c => -c), down = frame.v;
    const plus = (a, b) => a.map((c, k) => c + b[k]);
    const dirs = { L: left, R: right, U: up, D: down,
      UL: plus(up, left), UR: plus(up, right), DL: plus(down, left), DR: plus(down, right) };
    const connects = {};
    for (const [name, d] of Object.entries(dirs)) {
      connects[name] = connectsTo(ctx, definition, block.key, block.mesh.t[q], near(d));
    }
    return { definition, connects };
  });
}

function signature(states) {
  return states.map(s => s === null ? 'x' : Array.isArray(s) ? '' : [0, 1, 2, 3].map(i => quarterTile(s.connects, i)).join('')).join('|');
}

// The mesh drawn for a position whose quads are in these states, in the
// game data's mesh shape ({t, p, uv, c}: Layouts.java).
function meshFor(block, frames, states) {
  const m = block.mesh;
  const out = { t: [], p: [], uv: [], c: [] };
  // Quad q with its texture coordinates moved by uvAt (a quarter's
  // corners), the positions following them on the face.
  const copy = (q, tile, uvAt) => {
    out.t.push(tile);
    const f = frames[q];
    for (let i = 0; i < 4; i++) {
      const v = q * 4 + i;
      const [u0, v0] = [m.uv[2 * v], m.uv[2 * v + 1]];
      const [u1, v1] = uvAt(u0, v0);
      if (f && (u1 !== u0 || v1 !== v0)) {
        for (let k = 0; k < 3; k++) out.p.push(Math.round(f.origin[k] + u1 * f.U[k] + v1 * f.V[k]));
      } else {
        out.p.push(m.p[3 * v], m.p[3 * v + 1], m.p[3 * v + 2]);
      }
      out.uv.push(u1, v1);
      out.c.push(m.c[v]);
    }
  };
  states.forEach((state, q) => {
    if (state === null) return;
    if (Array.isArray(state)) {
      copy(q, m.t[q], (u, v) => [u, v]);
      return;
    }
    const tiles = [0, 1, 2, 3].map(i => state.definition.tiles[quarterTile(state.connects, i)]);
    if (tiles.every(t => t === tiles[0])) {
      copy(q, tiles[0], (u, v) => [u, v]);
      return;
    }
    QUARTERS.forEach(([ua, ub, va, vb], i) => {
      // Each corner of the quad becomes the same corner of the quarter,
      // so the quarter faces the same way.
      copy(q, tiles[i], (u, v) => [u === 0 ? ua : ub, v === 0 ? va : vb]);
    });
  });
  return out;
}

// A block's positions grouped by how its faces join up, each group with
// the mesh to draw them with: [{mesh, positions}]. One group, the block's
// own mesh, for a block with nothing to join or leave out.
export function connectedGroups(block, ctx) {
  const all = [{ mesh: block.mesh, positions: block.positions }];
  if (!block.mesh) return all;
  const quads = block.mesh.t.length;
  const frames = [];
  for (let q = 0; q < quads; q++) frames.push(quadFrame(block.mesh, q));
  const relevant = block.translucent
    || frames.some((f, q) => f && definitionFor(ctx, block.key, block.mesh.t[q], f.face));
  if (!relevant) return all;
  const groups = new Map();
  const p = block.positions;
  for (let i = 0; i < p.length; i += 3) {
    const states = quadStates(ctx, block, frames, p[i], p[i + 1], p[i + 2]);
    const key = signature(states);
    let group = groups.get(key);
    if (!group) {
      group = { mesh: meshFor(block, frames, states), positions: [] };
      groups.set(key, group);
    }
    group.positions.push(p[i], p[i + 1], p[i + 2]);
  }
  return [...groups.values()];
}
