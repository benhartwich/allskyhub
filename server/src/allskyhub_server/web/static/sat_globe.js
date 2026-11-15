// Draft D: Satellites 3D (satellites_3d.html). The hub computes everything in the Earth-fixed
// frame in Earth radii (devices/sat_globe.py); this draws it with three.js 0.186.1
// (static/three, MIT): the Earth with day and night, the camera with the circle it sees,
// the bright satellites and the paths of those above its horizon. Own small orbit controls
// (drag, wheel, pinch) instead of three's addons, so nothing needs an import map.
import * as THREE from "./three/three.module.js";

const box = document.getElementById("globe");
const statusLine = document.getElementById("globe-status");
const list = document.getElementById("globe-list");
const count = document.getElementById("globe-count");

const COLORS = { eye: 0x5fd3ff, sun: 0xf3b54a, shadow: 0x6b7397, cam: 0xb8ff8a };
const REFRESH_MS = 60_000;
const COVER_DEG = 14.06; // 500 km high, 10° up: acos(R cos 10° / (R + 500 km)) − 10°

// Earth-fixed (x to 0°/0°, z to the north pole) → three.js (y up): (x, z, −y). An
// equirectangular texture on SphereGeometry has longitude 0 at +x and east at −z.
const v3 = ([x, y, z]) => new THREE.Vector3(x, z, -y);

function webgl() {
  try {
    const c = document.createElement("canvas");
    return Boolean(c.getContext("webgl2") || c.getContext("webgl"));
  } catch {
    return false;
  }
}

function kind(s) {
  if (s.naked_eye) return "eye";
  return s.sunlit ? "sun" : "shadow";
}

const COMPASS = ["N", "NO", "O", "SO", "S", "SW", "W", "NW"];
const compass = (az) => COMPASS[Math.round(az / 45) % 8];

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

let data = null;
let chosen = null;
// The globe registers here when WebGL works; without it the list still works.
const globe = { build() {}, lookAt() {}, ready: false };

function fillList() {
  const up = data.satellites.filter((s) => s.up).sort((a, b) => b.altitude - a.altitude);
  count.textContent = `· ${up.length}`;
  list.innerHTML = up.map((s) => `<li class="sat-row" data-norad="${s.norad}" tabindex="0" aria-current="${s.norad === chosen}">
    <b>${escapeHtml(s.name)}</b><span class="when">${Math.round(s.altitude)}° ${compass(s.azimuth)} · ${s.height_km} km hoch</span>
    <span class="tags">${s.naked_eye ? '<span class="st eye">mit bloßem Auge</span>' : ""}${s.sunlit ? '<span class="st sun">sonnenbeschienen</span>' : '<span class="st shadow">im Erdschatten</span>'}${s.altitude >= 10 ? '<span class="st cam">im Bild</span>' : ""}</span></li>`).join("")
    || '<li class="muted">Gerade ist kein heller Satellit über dem Horizont.</li>';
}

function choose(norad) {
  chosen = chosen === norad ? null : norad;
  const s = data.satellites.find((x) => x.norad === chosen);
  if (s) globe.lookAt(s.now);
  globe.build();
  fillList();
}

async function load() {
  try {
    const answer = await fetch(box.dataset.src, { cache: "no-store" });
    if (!answer.ok) throw new Error(String(answer.status));
    const first = data === null;
    data = await answer.json();
    if (first) globe.lookAt(data.camera);
    globe.build();
    fillList();
    const at = new Date(data.at);
    const note = globe.ready ? "" : " · Dieser Browser kann kein WebGL: nur die Liste.";
    statusLine.textContent = (data.satellites.length
      ? `Stand ${at.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })} Uhr · ${data.satellites.length} helle Satelliten`
      : "Die Bahndaten werden geladen; gleich noch einmal.") + note;
  } catch {
    statusLine.textContent = "Die Satelliten konnten nicht geladen werden; neuer Versuch in einer Minute.";
  }
}

if (box) {
  list.addEventListener("click", (e) => {
    const row = e.target.closest("[data-norad]");
    if (row) choose(Number(row.dataset.norad));
  });
  list.addEventListener("keydown", (e) => {
    const row = e.target.closest("[data-norad]");
    if (row && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      choose(Number(row.dataset.norad));
    }
  });
}

if (box && webgl()) {
  globe.ready = true;
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  box.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(40, 1, 0.01, 100);

  // The Earth: day where the sun shines, the lights of the night elsewhere.
  const loader = new THREE.TextureLoader();
  const day = loader.load(box.dataset.day);
  const night = loader.load(box.dataset.night);
  day.colorSpace = THREE.SRGBColorSpace;
  night.colorSpace = THREE.SRGBColorSpace;
  const earthMaterial = new THREE.ShaderMaterial({
    uniforms: { day: { value: day }, night: { value: night }, sun: { value: new THREE.Vector3(1, 0, 0) } },
    vertexShader: `
      varying vec2 vUv;
      varying vec3 vNormal;
      void main() {
        vUv = uv;
        vNormal = normalize((modelMatrix * vec4(normal, 0.0)).xyz);
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }`,
    fragmentShader: `
      uniform sampler2D day;
      uniform sampler2D night;
      uniform vec3 sun;
      varying vec2 vUv;
      varying vec3 vNormal;
      void main() {
        float light = smoothstep(-0.08, 0.18, dot(normalize(vNormal), sun));
        vec3 lit = texture2D(day, vUv).rgb;
        vec3 dark = texture2D(night, vUv).rgb * 1.6 + vec3(0.004, 0.006, 0.016);
        gl_FragColor = vec4(mix(dark, lit, light), 1.0);
        #include <colorspace_fragment>
      }`,
  });
  scene.add(new THREE.Mesh(new THREE.SphereGeometry(1, 96, 64), earthMaterial));
  // A thin blue rim.
  scene.add(new THREE.Mesh(
    new THREE.SphereGeometry(1.015, 64, 48),
    new THREE.MeshBasicMaterial({ color: 0x5b8cff, transparent: true, opacity: 0.12, side: THREE.BackSide }),
  ));

  const dynamic = new THREE.Group();
  scene.add(dynamic);
  const labels = document.createElement("div");
  box.appendChild(labels);

  // Orbit around the Earth's centre.
  const view = { lat: 0.8, lon: 0.2, dist: 3.4 };
  const place = () => {
    const c = Math.cos(view.lat);
    camera.position.set(view.dist * c * Math.cos(view.lon), view.dist * Math.sin(view.lat), -view.dist * c * Math.sin(view.lon));
    camera.lookAt(0, 0, 0);
  };
  const lookAt = (p) => {
    const v = v3(p).normalize();
    view.lat = Math.asin(v.y);
    view.lon = Math.atan2(-v.z, v.x);
    place();
  };
  const pointers = new Map();
  let pinch = 0;
  box.addEventListener("pointerdown", (e) => {
    box.setPointerCapture(e.pointerId);
    pointers.set(e.pointerId, [e.clientX, e.clientY]);
  });
  box.addEventListener("pointermove", (e) => {
    const last = pointers.get(e.pointerId);
    if (!last) return;
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
      pointers.set(e.pointerId, [e.clientX, e.clientY]);
      const [a2, b2] = [...pointers.values()];
      const d2 = Math.hypot(a2[0] - b2[0], a2[1] - b2[1]);
      if (pinch && d) view.dist = Math.min(14, Math.max(1.4, view.dist * (d / d2)));
      pinch = d2;
    } else {
      const speed = 0.005 * (view.dist - 0.9) / 2.5;
      view.lon -= (e.clientX - last[0]) * speed;
      view.lat = Math.max(-1.5, Math.min(1.5, view.lat + (e.clientY - last[1]) * speed));
      pointers.set(e.pointerId, [e.clientX, e.clientY]);
    }
    place();
  });
  const up = (e) => {
    pointers.delete(e.pointerId);
    if (pointers.size < 2) pinch = 0;
  };
  box.addEventListener("pointerup", up);
  box.addEventListener("pointercancel", up);
  box.addEventListener("wheel", (e) => {
    e.preventDefault();
    view.dist = Math.min(14, Math.max(1.4, view.dist * Math.exp(e.deltaY * 0.001)));
    place();
  }, { passive: false });

  function circleAround(center, deg) {
    // Points at the angle ``deg`` around ``center`` on the sphere, a little above it.
    const c = v3(center).normalize();
    const a = Math.abs(c.y) < 0.9 ? new THREE.Vector3(0, 1, 0) : new THREE.Vector3(1, 0, 0);
    const u = new THREE.Vector3().crossVectors(c, a).normalize();
    const w = new THREE.Vector3().crossVectors(c, u);
    const r = (deg * Math.PI) / 180;
    const pts = [];
    for (let i = 0; i <= 96; i++) {
      const t = (i / 96) * Math.PI * 2;
      pts.push(c.clone().multiplyScalar(Math.cos(r)).add(u.clone().multiplyScalar(Math.sin(r) * Math.cos(t))).add(w.clone().multiplyScalar(Math.sin(r) * Math.sin(t))).multiplyScalar(1.003));
    }
    return pts;
  }

  function build() {
    dynamic.clear();
    labels.replaceChildren();
    if (!data) return;
    earthMaterial.uniforms.sun.value.copy(v3(data.sun).normalize());
    const cam = v3(data.camera);
    const marker = new THREE.Mesh(new THREE.SphereGeometry(0.018, 16, 12), new THREE.MeshBasicMaterial({ color: COLORS.cam }));
    marker.position.copy(cam.clone().multiplyScalar(1.004));
    dynamic.add(marker);
    dynamic.add(new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(circleAround(data.camera, COVER_DEG)),
      new THREE.LineBasicMaterial({ color: 0x9d7bff, transparent: true, opacity: 0.8 }),
    ));
    const positions = [];
    const colors = [];
    const color = new THREE.Color();
    for (const s of data.satellites) {
      const p = v3(s.now);
      positions.push(p.x, p.y, p.z);
      color.setHex(COLORS[kind(s)]);
      colors.push(color.r, color.g, color.b);
      if (s.track.length > 1) {
        const strong = chosen === s.norad;
        dynamic.add(new THREE.Line(
          new THREE.BufferGeometry().setFromPoints(s.track.map(v3)),
          new THREE.LineBasicMaterial({ color: COLORS[kind(s)], transparent: true, opacity: chosen === null ? 0.55 : strong ? 1 : 0.15 }),
        ));
      }
    }
    const points = new THREE.BufferGeometry();
    points.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    points.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
    dynamic.add(new THREE.Points(points, new THREE.PointsMaterial({ size: 4, sizeAttenuation: false, vertexColors: true })));
    for (const s of data.satellites.filter((x) => x.up || x.norad === chosen)) {
      const el = document.createElement("span");
      el.className = `globe-label ${kind(s)}`;
      el.textContent = s.name;
      el.dataset.pos = JSON.stringify(s.now);
      labels.appendChild(el);
    }
    const home = document.createElement("span");
    home.className = "globe-label cam";
    home.textContent = box.dataset.name || "Kamera";
    home.dataset.pos = JSON.stringify(data.camera);
    labels.appendChild(home);
  }

  globe.build = build;
  globe.lookAt = lookAt;

  const tmp = new THREE.Vector3();
  function frame() {
    const w = box.clientWidth;
    const h = box.clientHeight;
    if (renderer.domElement.width !== Math.round(w * renderer.getPixelRatio()) || renderer.domElement.height !== Math.round(h * renderer.getPixelRatio())) {
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    }
    renderer.render(scene, camera);
    for (const el of labels.children) {
      const p = v3(JSON.parse(el.dataset.pos));
      // Hidden behind the Earth: the point's side faces away and it is within the shadow cone.
      tmp.copy(p).sub(camera.position);
      const toCenter = camera.position.clone().negate();
      const behind = p.length() < 1.6 && p.clone().normalize().dot(camera.position.clone().normalize()) < 0.15 && tmp.length() > toCenter.length();
      tmp.copy(p).project(camera);
      const visible = !behind && tmp.z < 1;
      el.style.display = visible ? "" : "none";
      if (visible) {
        el.style.left = `${((tmp.x + 1) / 2) * w}px`;
        el.style.top = `${((1 - tmp.y) / 2) * h}px`;
      }
    }
    requestAnimationFrame(frame);
  }

  place();
  requestAnimationFrame(frame);
}

if (box) {
  load();
  setInterval(load, REFRESH_MS);
}
