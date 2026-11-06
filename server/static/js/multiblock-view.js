// The multiblock kit's 3D view: the multiblock as configured, block by
// block, from /api/multiblocks/layout (gcm/multiblocks.py layout()).
// Each kind of block is one instanced mesh of the geometry the game draws
// for it, textured from the game data's block atlas (layouts.png). Where
// that couldn't be captured it's a box with the block's face textures, or
// failing those its item icon. Drag to turn, scroll or pinch to zoom,
// and the layer slider hides everything above a layer, as NEI's preview
// does.
//
// three.js is only loaded the first time the view opens. The scene is
// drawn on demand (after a change or a drag), not every frame.

import { connectedGroups, ctmContext } from './ctm.js';
import { iconUrl } from './util.js';

const THREE_URL = '/static/vendor/three-0.170.0.module.min.js';
// Minecraft's face order (down, up, north, south, west, east) as three's
// box groups list them: +x east, -x west, +y up, -y down, +z south, -z north.
const BOX_FACES = [5, 4, 1, 0, 3, 2];

let THREE = null;
let view = null;          // {renderer, scene, camera, ...} once made
let shown = null;         // the layout drawn: {data, meshes, bounds}
let layoutSeq = 0;
const textures = new Map();  // url -> Promise<Texture>
let orbit = { yaw: Math.PI / 4, pitch: 0.6, distance: 30, target: [0, 0, 0] };
let layer = Infinity;     // the highest layer drawn

async function loadThree() {
  if (!THREE) THREE = await import(THREE_URL);
  return THREE;
}

function texture(url) {
  if (!textures.has(url)) {
    textures.set(url, new Promise((resolve) => {
      new THREE.TextureLoader().load(url, (t) => {
        t.magFilter = THREE.NearestFilter;
        t.minFilter = THREE.NearestFilter;
        t.generateMipmaps = false;
        t.colorSpace = THREE.SRGBColorSpace;
        resolve(t);
      }, undefined, () => resolve(null));
    }));
  }
  return textures.get(url);
}

function makeView() {
  const canvas = document.getElementById('kitCanvas');
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  } catch (e) {
    return null;
  }
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  const scene = new THREE.Scene();
  scene.add(new THREE.AmbientLight(0xffffff, 1.6));
  const sun = new THREE.DirectionalLight(0xffffff, 1.4);
  sun.position.set(0.5, 1, 0.8);
  scene.add(sun);
  const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 2000);
  const v = { renderer, scene, camera, canvas, sun };
  new ResizeObserver(() => resize(v)).observe(canvas.parentElement);
  bindOrbit(v);
  resize(v);
  return v;
}

function resize(v) {
  const box = v.canvas.parentElement.getBoundingClientRect();
  if (!box.width) return;
  const height = Math.round(Math.min(420, Math.max(240, box.width * 0.6)));
  v.renderer.setSize(box.width, height, true);
  v.camera.aspect = box.width / height;
  v.camera.updateProjectionMatrix();
  draw();
}

function draw() {
  if (!view) return;
  const { camera } = view;
  const [tx, ty, tz] = orbit.target;
  const cos = Math.cos(orbit.pitch);
  camera.position.set(
    tx + orbit.distance * cos * Math.sin(orbit.yaw),
    ty + orbit.distance * Math.sin(orbit.pitch),
    tz + orbit.distance * cos * Math.cos(orbit.yaw),
  );
  camera.lookAt(tx, ty, tz);
  view.renderer.render(view.scene, camera);
}

// Drag to turn, wheel or two-finger pinch to zoom.
function bindOrbit(v) {
  const pointers = new Map();
  let pinch = null;
  v.canvas.addEventListener('pointerdown', (e) => {
    v.canvas.setPointerCapture(e.pointerId);
    pointers.set(e.pointerId, [e.clientX, e.clientY]);
    pinch = null;
  });
  v.canvas.addEventListener('pointermove', (e) => {
    const last = pointers.get(e.pointerId);
    if (!last) return;
    if (pointers.size === 1) {
      orbit.yaw -= (e.clientX - last[0]) * 0.01;
      orbit.pitch = Math.max(-1.5, Math.min(1.5, orbit.pitch + (e.clientY - last[1]) * 0.01));
    }
    pointers.set(e.pointerId, [e.clientX, e.clientY]);
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
      if (pinch) zoom(pinch / d);
      pinch = d;
    }
    draw();
  });
  const up = (e) => { pointers.delete(e.pointerId); pinch = null; };
  v.canvas.addEventListener('pointerup', up);
  v.canvas.addEventListener('pointercancel', up);
  v.canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    zoom(Math.exp(e.deltaY * 0.001));
    draw();
  }, { passive: false });
}

function zoom(factor) {
  const max = shown ? shown.bounds.radius * 6 : 500;
  orbit.distance = Math.max(2, Math.min(max, orbit.distance * factor));
}

// The geometry the game draws for a block (BlockMeshes.java's mesh):
// quads with positions in 1/256 of a block from its corner, texture
// coordinates in 1/256 of an atlas tile, and the game's colour at each
// vertex (its face shading and tint), so it's drawn unlit.
function meshGeometry(mesh, columns, tile, atlasHeight, atlasWidth) {
  const quads = mesh.t.length;
  const position = new Float32Array(quads * 12);
  const uv = new Float32Array(quads * 8);
  const color = new Float32Array(quads * 12);
  const index = [];
  const c = new THREE.Color();
  for (let q = 0; q < quads; q++) {
    const t = mesh.t[q];
    const x0 = (t % columns) * tile, y0 = Math.floor(t / columns) * tile;
    for (let i = 0; i < 4; i++) {
      const v = q * 4 + i;
      // Centred like the cubes, which the instances' positions assume.
      for (let a = 0; a < 3; a++) position[3 * v + a] = mesh.p[3 * v + a] / 256 - 0.5;
      uv[2 * v] = (x0 + mesh.uv[2 * v] / 256 * tile) / atlasWidth;
      uv[2 * v + 1] = 1 - (y0 + mesh.uv[2 * v + 1] / 256 * tile) / atlasHeight;
      c.setHex(mesh.c[v], THREE.SRGBColorSpace);
      color[3 * v] = c.r; color[3 * v + 1] = c.g; color[3 * v + 2] = c.b;
    }
    const v = q * 4;
    index.push(v, v + 1, v + 2, v, v + 2, v + 3);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(position, 3));
  geometry.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
  geometry.setAttribute('color', new THREE.BufferAttribute(color, 3));
  geometry.setIndex(index);
  return geometry;
}

// A box whose faces each show one atlas tile (faces: Minecraft order).
function boxGeometry(faces, columns, tile, atlasHeight, atlasWidth) {
  const geometry = new THREE.BoxGeometry(1, 1, 1);
  const uv = geometry.getAttribute('uv');
  const rows = atlasHeight / tile;
  for (let group = 0; group < 6; group++) {
    const index = faces[BOX_FACES[group]];
    for (let i = 0; i < 4; i++) {
      const at = group * 4 + i;
      if (index < 0) {
        uv.setXY(at, 0, 0);  // the atlas's corner: a face that didn't draw stays see-through
        continue;
      }
      const u0 = (index % columns) / (atlasWidth / tile);
      const row = Math.floor(index / columns);
      const v0 = 1 - (row + 1) / rows;
      uv.setXY(at, u0 + uv.getX(at) / (atlasWidth / tile), v0 + uv.getY(at) / rows);
    }
  }
  uv.needsUpdate = true;
  return geometry;
}

function clearScene() {
  if (!shown) return;
  for (const mesh of shown.meshes) {
    view.scene.remove(mesh);
    mesh.geometry.dispose();
    mesh.material.dispose();
  }
  shown = null;
}

async function build(data) {
  const atlas = data.atlas ? await texture(data.atlas) : null;
  const meshes = [];
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  const matrix = new THREE.Matrix4();
  const ctx = ctmContext(data);
  const parts = [];
  for (const block of data.blocks) {
    const captured = !!(block.mesh && atlas);
    // A captured block whose faces join up (ctm.js) is drawn in groups,
    // one per way its faces join; anything else in one.
    const groups = captured ? connectedGroups(block, ctx) : [{ mesh: null, positions: block.positions }];
    for (const group of groups) parts.push({ block, captured, ...group });
  }
  for (const { block, captured, mesh: blockMesh, positions } of parts) {
    const count = positions.length / 3;
    let geometry;
    let map = null;
    if (captured) {
      geometry = meshGeometry(blockMesh, data.columns, data.tile, atlas.image.height, atlas.image.width);
      map = atlas;
    } else if (block.faces && atlas) {
      geometry = boxGeometry(block.faces, data.columns, data.tile, atlas.image.height, atlas.image.width);
      map = atlas;
    } else {
      geometry = new THREE.BoxGeometry(1, 1, 1);
      map = block.icon ? await texture(iconUrl(block.icon)) : null;
    }
    const options = {
      map,
      transparent: !!block.translucent,
      depthWrite: !block.translucent,
      alphaTest: block.translucent ? 0.02 : 0.5,
      side: block.translucent ? THREE.DoubleSide : THREE.FrontSide,
    };
    // A captured block has the game's shading in its colours; a cube is lit.
    const material = captured
      ? new THREE.MeshBasicMaterial({ ...options, vertexColors: true })
      : new THREE.MeshLambertMaterial({ ...options, color: map ? (block.tint ?? 0xffffff) : 0x8a8f98 });
    const mesh = new THREE.InstancedMesh(geometry, material, count);
    const ys = [];
    for (let i = 0; i < count; i++) {
      const x = positions[3 * i], y = positions[3 * i + 1], z = positions[3 * i + 2];
      matrix.makeTranslation(x, y, z);
      mesh.setMatrixAt(i, matrix);
      ys.push(y);
      if (x < min[0]) min[0] = x; if (y < min[1]) min[1] = y; if (z < min[2]) min[2] = z;
      if (x > max[0]) max[0] = x; if (y > max[1]) max[1] = y; if (z > max[2]) max[2] = z;
    }
    mesh.instanceMatrix.needsUpdate = true;
    // Glass last, so what's behind it is drawn first.
    mesh.renderOrder = block.translucent ? 1 : 0;
    mesh.userData = { ys, key: block.key };
    meshes.push(mesh);
  }
  const size = [0, 1, 2].map(i => max[i] - min[i] + 1);
  return {
    data,
    meshes,
    bounds: {
      min, max,
      center: [0, 1, 2].map(i => (min[i] + max[i]) / 2),
      radius: Math.max(1, Math.hypot(...size) / 2),
    },
  };
}

// Shows the blocks up to the layer: each mesh's positions are sorted by
// height, so it's how many of them to draw.
function applyLayer() {
  if (!shown) return;
  for (const mesh of shown.meshes) {
    const ys = mesh.userData.ys;
    let n = ys.length;
    while (n > 0 && ys[n - 1] > layer) n--;
    mesh.count = n;
  }
  const slider = document.getElementById('kitLayer');
  const { min, max } = shown.bounds;
  const top = Math.min(layer, max[1]);
  document.getElementById('kitLayerText').textContent = top >= max[1]
    ? 'All layers' : `Layer ${top - min[1] + 1} of ${max[1] - min[1] + 1}`;
  slider.value = String(top);
  draw();
}

function setMessage(text) {
  const el = document.getElementById('kitViewMessage');
  el.textContent = text || '';
  el.hidden = !text;
}

// Draws the multiblock key at tiers ({channel: tier}). The camera and
// layer stay where they were when it's the same multiblock (a tier
// changed), and are reset for another one.
export async function showLayout(key, tiers) {
  const seq = ++layoutSeq;
  setMessage(shown ? '' : 'Loading…');
  try {
    await loadThree();
  } catch (e) {
    setMessage('Could not load the 3D view.');
    return;
  }
  if (!view) view = makeView();
  if (!view) {
    setMessage('This browser can’t show 3D here (no WebGL).');
    return;
  }
  const params = new URLSearchParams({ key });
  for (const [name, tier] of Object.entries(tiers)) params.set('tier.' + name, String(tier));
  let data;
  try {
    const res = await fetch('/api/multiblocks/layout?' + params.toString());
    if (!res.ok) throw new Error(String(res.status));
    data = await res.json();
  } catch (e) {
    if (seq === layoutSeq) setMessage('Could not load the layout.');
    return;
  }
  const built = await build(data);
  if (seq !== layoutSeq) {
    for (const mesh of built.meshes) { mesh.geometry.dispose(); mesh.material.dispose(); }
    return;
  }
  const same = shown && shown.key === key;
  clearScene();
  shown = { ...built, key };
  for (const mesh of shown.meshes) view.scene.add(mesh);
  const { min, max, center, radius } = shown.bounds;
  const slider = document.getElementById('kitLayer');
  slider.min = String(min[1]);
  slider.max = String(max[1]);
  if (!same) {
    orbit = { yaw: Math.PI / 4, pitch: 0.55, distance: radius * 2.6, target: center };
    layer = Infinity;
  } else {
    orbit.target = center;
  }
  view.canvas.dataset.blocks = String(data.blocks.reduce((n, b) => n + b.positions.length / 3, 0));
  setMessage('');
  resize(view);
  applyLayer();
}

export function hideLayout() {
  layoutSeq++;
  if (view) clearScene();
}

export function setupLayoutView() {
  document.getElementById('kitLayer').addEventListener('input', (e) => {
    layer = Number(e.target.value);
    if (shown && layer >= shown.bounds.max[1]) layer = Infinity;
    applyLayer();
  });
}
