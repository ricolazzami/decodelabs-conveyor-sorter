// Live 3D view of the conveyor sorter.
//
// Truth: box positions and lane decisions come from the plant simulator (there
// are no physical sensors); motor, valves, state, counters and fault are the
// PLC's own outputs and words. The view never invents a box or a lane.
//
// Served by sim/demo3d.py, which also feeds /state at about 16 Hz. Add ?rec=1
// and the page leaves the HMI area empty and exposes window.__recStart /
// __recStop: sim/demo3d.py records the canvas through MediaRecorder and lays
// the real FUXA screen over the empty area afterwards.
import * as THREE from "three";

const MM = 0.001;
const BELT_TOP = 0.75;            // top face of the belt
const BELT_LEN = 2.20;            // belt runs x = 0 .. 2.20
const BELT_HALF = 0.23;           // belt is 460 mm wide
const OUT_END = 3.05;             // end of the small-lane outfeed
const CHUTE_TOP = 0.60;           // accumulation chutes sit 150 mm lower
const LANE_Z = 0.60;              // centre of a side chute
const BOX_W = 0.26, BOX_D = 0.24;
const STATION = { large: 0.60, reject: 1.20 };   // pusher 1 and pusher 2

const stage = document.getElementById("stage");
const caption = document.getElementById("caption");

// ------------------------------------------------------------------ renderer
const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true,
  powerPreference: "high-performance" });
renderer.setPixelRatio(1);
renderer.setSize(innerWidth, innerHeight);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.18;
stage.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x10161d);
scene.fog = new THREE.Fog(0x10161d, 6.0, 14.0);

const camera = new THREE.PerspectiveCamera(32, innerWidth / innerHeight, 0.05, 40);
const target = new THREE.Vector3(1.45, 0.30, 0);
let azimuth = 0.46, elevation = 0.80, radius = 5.15;

scene.add(new THREE.HemisphereLight(0xc6dcff, 0x141b23, 1.15));
const key = new THREE.DirectionalLight(0xfff3e2, 1.55);
key.position.set(2.2, 4.0, 2.6);
scene.add(key);
const rim = new THREE.DirectionalLight(0x6fa8ff, 0.55);
rim.position.set(-2.6, 1.8, -2.4);
scene.add(rim);

// ------------------------------------------------------------------ helpers
const matSteel = new THREE.MeshLambertMaterial({ color: 0x9aa6b3 });
const matDark = new THREE.MeshLambertMaterial({ color: 0x2c343d });
const matBelt = new THREE.MeshLambertMaterial({ color: 0x2b333d });
const matChute = new THREE.MeshLambertMaterial({ color: 0x4c6076 });
const matCard = new THREE.MeshLambertMaterial({ color: 0xc08f5e });
const matCardTop = new THREE.MeshLambertMaterial({ color: 0xd3a374 });

function box(w, h, d, mat) { return new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat); }

function add(mesh, x, y, z) { mesh.position.set(x, y, z); scene.add(mesh); return mesh; }

// soft round blob used as a fake contact shadow
function blobTexture() {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d");
  const grad = g.createRadialGradient(64, 64, 4, 64, 64, 62);
  grad.addColorStop(0, "rgba(0,0,0,0.62)");
  grad.addColorStop(0.55, "rgba(0,0,0,0.26)");
  grad.addColorStop(1, "rgba(0,0,0,0)");
  g.fillStyle = grad;
  g.fillRect(0, 0, 128, 128);
  return new THREE.CanvasTexture(c);
}
const blobTex = blobTexture();
function blob(w, d) {
  const m = new THREE.Mesh(new THREE.PlaneGeometry(w, d),
    new THREE.MeshBasicMaterial({ map: blobTex, transparent: true, depthWrite: false }));
  m.rotation.x = -Math.PI / 2;
  return m;
}

function tagSprite(text, colour, size = 0.30) {
  const c = document.createElement("canvas");
  c.width = 256; c.height = 96;
  const g = c.getContext("2d");
  g.font = "bold 56px DejaVu Sans, sans-serif";
  g.textAlign = "center"; g.textBaseline = "middle";
  g.fillStyle = "rgba(6,12,20,0.78)";
  const w = Math.min(248, g.measureText(text).width + 26);
  g.fillRect(128 - w / 2, 14, w, 68);
  g.fillStyle = colour;
  g.fillText(text, 128, 50);
  const s = new THREE.Sprite(new THREE.SpriteMaterial({
    map: new THREE.CanvasTexture(c), transparent: true, depthWrite: false }));
  s.scale.set(size, size * 0.375, 1);
  return s;
}

// ------------------------------------------------------------------ the floor
add(new THREE.Mesh(new THREE.PlaneGeometry(24, 24),
  new THREE.MeshLambertMaterial({ color: 0x141a22 })), 0, 0, 0).rotation.x = -Math.PI / 2;
const grid = new THREE.GridHelper(12, 24, 0x2a3849, 0x1d2632);
grid.position.y = 0.002;
grid.material.transparent = true;
grid.material.opacity = 0.35;
scene.add(grid);

add(blob(4.6, 2.0), 1.35, 0.004, 0).material.opacity = 0.9;

// ------------------------------------------------------------------ the line
const machine = new THREE.Group();
scene.add(machine);

const belt = box(BELT_LEN, 0.035, BELT_HALF * 2, matBelt);
belt.position.set(BELT_LEN / 2, BELT_TOP - 0.0175, 0);
machine.add(belt);

const slatCanvas = document.createElement("canvas");
slatCanvas.width = 128; slatCanvas.height = 32;      // bands along u: slats sit across the belt
{
  const g = slatCanvas.getContext("2d");
  g.fillStyle = "#333b45"; g.fillRect(0, 0, 128, 32);
  g.fillStyle = "#12171d"; g.fillRect(0, 0, 19, 32);
  g.fillStyle = "#4d5865"; g.fillRect(19, 0, 4, 32);
}
const slatTex = new THREE.CanvasTexture(slatCanvas);
slatTex.wrapS = slatTex.wrapT = THREE.RepeatWrapping;
slatTex.repeat.set(30, 1);
slatTex.colorSpace = THREE.SRGBColorSpace;
const slats = new THREE.Mesh(new THREE.PlaneGeometry(BELT_LEN, BELT_HALF * 2),
  new THREE.MeshLambertMaterial({ map: slatTex }));
slats.rotation.x = -Math.PI / 2;
slats.position.set(BELT_LEN / 2, BELT_TOP + 0.001, 0);
machine.add(slats);

for (const z of [-BELT_HALF - 0.018, BELT_HALF + 0.018]) {
  const rail = box(BELT_LEN, 0.055, 0.03, matSteel);
  rail.position.set(BELT_LEN / 2, BELT_TOP + 0.012, z);
  machine.add(rail);
}
for (const z of [-BELT_HALF, BELT_HALF]) {                 // frame skirt
  const skirt = box(BELT_LEN, 0.11, 0.022, matDark);
  skirt.position.set(BELT_LEN / 2, BELT_TOP - 0.09, z);
  machine.add(skirt);
}
for (const x of [0.10, 0.76, 1.42, 2.10]) {
  for (const z of [-0.19, 0.19]) {
    const leg = box(0.05, BELT_TOP - 0.14, 0.05, matDark);
    leg.position.set(x, (BELT_TOP - 0.14) / 2, z);
    machine.add(leg);
  }
}
{ // drive motor at the head end
  const motor = new THREE.Mesh(new THREE.CylinderGeometry(0.075, 0.075, 0.16, 18), matSteel);
  motor.rotation.z = Math.PI / 2;
  motor.position.set(2.30, BELT_TOP - 0.10, -0.31);
  machine.add(motor);
  const gear = box(0.12, 0.12, 0.12, matDark);
  gear.position.set(2.20, BELT_TOP - 0.10, -0.31);
  machine.add(gear);
}

// the straight lane: outfeed rollers, same height, boxes leave the frame there
{
  const top = box(OUT_END - BELT_LEN, 0.03, BELT_HALF * 2, matBelt);
  top.position.set((BELT_LEN + OUT_END) / 2, BELT_TOP - 0.015, 0);
  machine.add(top);
  for (const z of [-BELT_HALF - 0.018, BELT_HALF + 0.018]) {
    const rail = box(OUT_END - BELT_LEN, 0.05, 0.028, matSteel);
    rail.position.set((BELT_LEN + OUT_END) / 2, BELT_TOP + 0.010, z);
    machine.add(rail);
  }
  for (const x of [2.34, 2.98]) {
    for (const z of [-0.19, 0.19]) {
      const leg = box(0.045, BELT_TOP - 0.14, 0.045, matDark);
      leg.position.set(x, (BELT_TOP - 0.14) / 2, z);
      machine.add(leg);
    }
  }
}

// side chutes: where pusher 1 and pusher 2 put a box, with a low rail so the
// queue reads as a chute and not as a table top
function chute(x, dir, colour) {                           // dir = -1 lane B, +1 lane C
  const zc = dir * (LANE_Z + 0.36);
  const deck = box(0.58, 0.028, 1.02, new THREE.MeshLambertMaterial({ color: colour }));
  deck.position.set(x, CHUTE_TOP - 0.014, zc);
  machine.add(deck);
  for (const dz of [-0.51, 0.51]) {
    const rail = box(0.58, 0.05, 0.025, matSteel);
    rail.position.set(x, CHUTE_TOP + 0.012, zc + dz);
    machine.add(rail);
  }
  for (const sx of [-0.27, 0.27]) {
    const rail = box(0.022, 0.05, 1.02, matSteel);
    rail.position.set(x + sx, CHUTE_TOP + 0.012, zc);
    machine.add(rail);
  }
  for (const dz of [-0.42, 0.42]) {
    for (const sx of [-0.22, 0.22]) {
      const leg = box(0.04, CHUTE_TOP - 0.02, 0.04, matDark);
      leg.position.set(x + sx, (CHUTE_TOP - 0.02) / 2, zc + dz);
      machine.add(leg);
    }
  }
  return new THREE.Vector3(x, CHUTE_TOP, zc);
}
const LANE_ANCHOR = { large: chute(STATION.large, -1, 0x574534),
                      reject: chute(STATION.reject, +1, 0x5a3a38) };


// ------------------------------------------------------------------ pushers
function pusher(x, dir) {                                  // dir = -1 pushes to -z
  const g = new THREE.Group();
  g.position.x = x;
  const rest = dir * (BELT_HALF + 0.05);
  const body = box(0.12, 0.15, 0.28, matSteel);
  body.position.set(0, BELT_TOP + 0.055, -dir * (BELT_HALF + 0.24));
  g.add(body);
  const bracket = box(0.09, 0.24, 0.10, matDark);           // stands the body off the frame
  bracket.position.set(0, BELT_TOP - 0.11, -dir * (BELT_HALF + 0.24));
  g.add(bracket);
  const plate = box(0.26, 0.17, 0.022, matSteel);
  plate.position.set(0, BELT_TOP + 0.085, rest);
  g.add(plate);
  const rod = new THREE.Mesh(new THREE.CylinderGeometry(0.014, 0.014, 1, 10), matSteel);
  rod.rotation.x = Math.PI / 2;
  g.add(rod);
  scene.add(g);
  return { g, plate, rod, dir, rest };
}
const P1 = pusher(STATION.large, -1);
const P2 = pusher(STATION.reject, +1);

// photo-eyes: two posts, one beam that turns bright when a box blocks it
function eye(x, tag) {
  const g = new THREE.Group();
  g.position.x = x;
  for (const z of [-BELT_HALF - 0.05, BELT_HALF + 0.05]) {
    const post = box(0.03, 0.26, 0.03, matDark);
    post.position.set(0, BELT_TOP + 0.13, z);
    g.add(post);
  }
  const beamMat = new THREE.MeshBasicMaterial({ color: 0x8d2222, transparent: true, opacity: 0.5 });
  const beam = new THREE.Mesh(new THREE.CylinderGeometry(0.005, 0.005, BELT_HALF * 2 + 0.10, 8), beamMat);
  beam.rotation.x = Math.PI / 2;
  beam.position.set(0, BELT_TOP + 0.07, 0);        // 70 mm up: every box breaks it
  g.add(beam);
  const s = tagSprite(tag, "#9ecbff", 0.26);
  s.position.set(0, BELT_TOP + 0.52, -BELT_HALF - 0.36);
  g.add(s);
  scene.add(g);
  return { g, beamMat };
}
const EYE1 = eye(STATION.large, "S1");
const EYE2 = eye(STATION.reject, "S2");

// ------------------------------------------------------------------ boxes
const boxes = new Map();          // id -> presentation record
const amberEdge = new THREE.LineBasicMaterial({ color: 0xffb545 });
const cardEdge = new THREE.LineBasicMaterial({ color: 0x6b4a2b, transparent: true, opacity: 0.75 });
let state = null;
let captionText = "";
const slotCount = { large: 0, reject: 0 };

function makeBox(b) {
  const h = b.size === "large" ? 0.22 : 0.12;
  const g = new THREE.Group();
  const side = box(BOX_W, h, BOX_D, matCard);
  side.position.y = h / 2;
  g.add(side);
  const lid = box(BOX_W * 0.98, 0.004, BOX_D * 0.98, matCardTop);
  lid.position.y = h + 0.001;
  g.add(lid);
  const edges = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.BoxGeometry(BOX_W, h, BOX_D)),
    b.uncertain ? amberEdge : cardEdge);
  edges.position.y = h / 2;
  g.add(edges);
  if (b.uncertain) {
    const q = tagSprite("?", "#ffcf7a", 0.16);
    q.position.set(0, h + 0.13, 0);
    g.add(q);
  }
  const sh = blob(BOX_W * 1.7, BOX_D * 1.7);
  sh.position.y = 0.003;
  g.add(sh);

  g.position.set(b.x * MM, BELT_TOP, 0);
  scene.add(g);
  boxes.set(b.id, { g, laneTagged: null, slot: null });
}

// ------------------------------------------------------------------ HUD
// The lane legend is drawn inside the canvas, not the DOM: the recording is the
// canvas only, so anything in the DOM would be missing from the video.
const hud = new THREE.Scene();
const hudCam = new THREE.OrthographicCamera(0, 1, 1, 0, -1, 1);

function legendTexture() {
  const c = document.createElement("canvas");
  c.width = 620; c.height = 132;
  const g = c.getContext("2d");
  g.fillStyle = "rgba(4,10,18,0.76)";
  g.fillRect(0, 0, 620, 132);
  g.fillStyle = "#30363d";
  g.fillRect(0, 0, 5, 132);
  const rows = [["#4fd06a", "A  SMALL", "straight on, out the end"],
                ["#f0a13a", "B  LARGE", "pusher 1 pushes left"],
                ["#e5615a", "C  REJECT", "pusher 2 pushes right"]];
  rows.forEach(([colour, name, what], i) => {
    const y = 20 + i * 36;
    g.fillStyle = colour;
    g.fillRect(22, y, 14, 14);
    g.font = "bold 22px DejaVu Sans, sans-serif";
    g.fillStyle = "#e6edf3";
    g.fillText(name, 50, y + 13);
    g.font = "22px DejaVu Sans, sans-serif";
    g.fillStyle = "#9fb0c0";
    g.fillText(what, 190, y + 13);
  });
  return new THREE.CanvasTexture(c);
}

const legendW = 620, legendH = 132, MARGIN = 22;
const legend = new THREE.Mesh(new THREE.PlaneGeometry(1, 1),
  new THREE.MeshBasicMaterial({ map: legendTexture(), transparent: true, depthTest: false }));
hud.add(legend);

function layoutHud() {
  legend.scale.set(legendW / innerWidth, legendH / innerHeight, 1);
  legend.position.set(1 - (legendW / 2 + MARGIN) / innerWidth,
                      1 - (legendH / 2 + MARGIN) / innerHeight, 0);
}
layoutHud();

// ------------------------------------------------------------------ state feed
async function poll() {
  try {
    const r = await fetch("/state", { cache: "no-store" });
    const s = await r.json();
    state = s;
    if (s.caption && s.caption !== captionText) {
      captionText = s.caption;
      caption.textContent = captionText;
    }
  } catch (e) {
    caption.textContent = "plant not answering…";
  }
  setTimeout(poll, 60);
}
poll();

// ------------------------------------------------------------------ camera
let dragging = false, lastX = 0, lastY = 0, idleSince = performance.now();
renderer.domElement.addEventListener("pointerdown", (e) => {
  dragging = true; lastX = e.clientX; lastY = e.clientY; idleSince = performance.now();
});
addEventListener("pointerup", () => { dragging = false; idleSince = performance.now(); });
addEventListener("pointermove", (e) => {
  if (!dragging) return;
  azimuth -= (e.clientX - lastX) * 0.005;
  elevation = Math.max(0.10, Math.min(1.25, elevation + (e.clientY - lastY) * 0.004));
  lastX = e.clientX; lastY = e.clientY;
});
renderer.domElement.addEventListener("wheel", (e) => {
  radius = Math.max(2.2, Math.min(8.0, radius + e.deltaY * 0.0022));
  e.preventDefault();
}, { passive: false });

// ------------------------------------------------------------------ frame
const clock = new THREE.Clock();
let beltTravel = 0;

function posePusher(p, extended, dt) {
  const reach = 0.30;                                   // plate travel into the lane
  const want = p.dir * (p.rest - reach * extended);
  p.plate.position.z += (want - p.plate.position.z) * Math.min(1, dt * 14);
  const back = -p.dir * (BELT_HALF + 0.24);             // face of the cylinder body
  p.rod.position.set(0, BELT_TOP + 0.085, (back + p.plate.position.z) / 2);
  p.rod.scale.y = Math.max(0.02, Math.abs(p.plate.position.z - back));
}

function frame() {
  const dt = Math.min(clock.getDelta(), 0.1);
  const now = performance.now();

  if (state) {
    if (state.motor) beltTravel += dt * 0.30;
    slatTex.offset.x = -beltTravel * (30 / BELT_LEN);     // one slat every 73 mm

    posePusher(P1, state.p1 ? 1 : 0, dt);
    posePusher(P2, state.p2 ? 1 : 0, dt);

    for (const [eye, blocked] of [[EYE1, state.s1], [EYE2, state.s2]]) {
      eye.beamMat.opacity = blocked ? 0.95 : 0.45;
      eye.beamMat.color.setHex(blocked ? 0xff3b30 : 0x8d2222);
    }

    for (const b of state.boxes) {
      if (!boxes.has(b.id)) makeBox(b);
      const o = boxes.get(b.id);
      const g = o.g;

      if (b.lane && o.laneTagged !== b.lane) {           // box just arrived in a lane
        o.laneTagged = b.lane;
        if (b.lane === "large" || b.lane === "reject") {
          o.slot = slotCount[b.lane]++;
          o.slotX = LANE_ANCHOR[b.lane].x;
          o.slotZ = (b.lane === "large" ? -1 : 1) * (LANE_Z + 0.14 + o.slot * 0.27);
        }
      }

      if (!b.lane) {
        const want = b.x * MM + BOX_W / 2;               // the plant's own position
        g.position.x += (want - g.position.x) * Math.min(1, dt * 18);
        g.position.z += (0 - g.position.z) * Math.min(1, dt * 10);
        g.position.y += (BELT_TOP - g.position.y) * Math.min(1, dt * 12);
      } else if (b.lane === "small") {
        // the straight lane keeps running: the plant stops tracking the box
        g.position.x += dt * 0.30;
        g.position.z += (0 - g.position.z) * Math.min(1, dt * 10);
        g.position.y += (BELT_TOP - g.position.y) * Math.min(1, dt * 12);
        if (g.position.x > OUT_END + 0.30) g.visible = false;
      } else {
        // pushed off the belt and rolling down its chute to a queue slot
        const k = Math.min(1, dt * 3.4);
        g.position.x += (o.slotX - g.position.x) * k;
        g.position.z += (o.slotZ - g.position.z) * k * 0.85;
        g.position.y += (CHUTE_TOP - g.position.y) * Math.min(1, dt * 6);
      }
      g.rotation.y += ((b.lane ? (b.lane === "large" ? 0.12 : -0.12) : 0) - g.rotation.y)
        * Math.min(1, dt * 3);
    }
  }

  if (state && state.zoom && !dragging) radius += (state.zoom - radius) * Math.min(1, dt * 0.5);
  if (!dragging && now - idleSince > 2200) azimuth -= dt * 0.016;   // slow drift, no cuts
  camera.position.set(
    target.x + radius * Math.cos(elevation) * Math.sin(azimuth),
    target.y + radius * Math.sin(elevation),
    target.z + radius * Math.cos(elevation) * Math.cos(azimuth));
  camera.lookAt(target);

  renderer.render(scene, camera);
  renderer.autoClear = false;                     // legend on top, no depth fight
  renderer.render(hud, hudCam);
  renderer.autoClear = true;
  requestAnimationFrame(frame);
}
frame();

addEventListener("resize", () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
  layoutHud();
});

// ------------------------------------------------------- recording hook
// The canvas is recorded at its source (10 fps, frames pushed on a timer);
// MediaRecorder stamps each frame with its capture time, so playback is real
// time even if the page renders faster.
let recorder = null, frameTimer = null;
const FRAME_MS = 100;

window.__recStart = async () => {
  await fetch("/rec/open", { method: "POST" });          // truncate the file
  const stream = renderer.domElement.captureStream(0);
  const track = stream.getVideoTracks()[0];
  recorder = new MediaRecorder(stream, { mimeType: "video/webm;codecs=vp9",
    videoBitsPerSecond: 12_000_000 });
  let chain = Promise.resolve();
  recorder.ondataavailable = (e) => {                     // chunks in order, one at a time
    if (!e.data.size) return;
    chain = chain.then(() => fetch("/rec/chunk", { method: "POST", body: e.data }));
  };
  recorder.onstop = () => {
    chain.then(() => fetch("/rec/done", { method: "POST" }));
  };
  recorder.start(2000);
  frameTimer = setInterval(() => track.requestFrame(), FRAME_MS);
  return true;
};

window.__recStop = () => {
  if (frameTimer) clearInterval(frameTimer);
  recorder.stop();
  return true;
};