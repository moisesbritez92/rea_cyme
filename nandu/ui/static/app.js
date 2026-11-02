'use strict';
/* Ñandu UI: unifilar en canvas, explorador, propiedades e informes con el estilo de CYME. */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const fmt = (v, d = 2) => (v === null || v === undefined || v === '') ? '' : Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });

const S = {
  file: null, geom: null, explorer: [], nodes: new Map(), sections: [], sectionById: new Map(),
  last: null, view: { s: 1, tx: 0, ty: 0 }, selection: null, hover: null, report: 'messages', options: {},
  grids: {}, popouts: new Map(),
  dpr: window.devicePixelRatio || 1,
};
const canvas = $('#view');
const ctx = canvas.getContext('2d');
const mapCanvas = $('#mapCanvas');
const mapCtx = mapCanvas.getContext('2d');
const MERCATOR_LIMIT = 20037508.342789244;
const tileCache = new Map();
const MAP_TILES = {
  streets: (z, x, y) => `https://tile.openstreetmap.org/${z}/${x}/${y}.png`,
  satellite: (z, x, y) => `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${z}/${y}/${x}`,
};

// Compatibilidad con servidores ya abiertos que todavía no entregan map_bounds.
function projectUtm(x, y, zone, south) {
  const a = 6378137, e2 = 0.0066943799901413165, ep2 = e2 / (1 - e2), k0 = 0.9996;
  const m = (y - (south ? 10000000 : 0)) / k0;
  const mu = m / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256));
  const e1 = (1 - Math.sqrt(1 - e2)) / (1 + Math.sqrt(1 - e2));
  const phi = mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * Math.sin(2 * mu)
    + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * Math.sin(4 * mu)
    + 151 * e1 ** 3 / 96 * Math.sin(6 * mu) + 1097 * e1 ** 4 / 512 * Math.sin(8 * mu);
  const c = ep2 * Math.cos(phi) ** 2, t = Math.tan(phi) ** 2;
  const n = a / Math.sqrt(1 - e2 * Math.sin(phi) ** 2);
  const r = a * (1 - e2) / (1 - e2 * Math.sin(phi) ** 2) ** 1.5;
  const d = (x - 500000) / (n * k0);
  const lat = phi - n * Math.tan(phi) / r * (d ** 2 / 2 - (5 + 3 * t + 10 * c - 4 * c ** 2 - 9 * ep2) * d ** 4 / 24
    + (61 + 90 * t + 298 * c + 45 * t ** 2 - 252 * ep2 - 3 * c ** 2) * d ** 6 / 720);
  const lon = (zone * 6 - 183) * Math.PI / 180 + (d - (1 + 2 * t + c) * d ** 3 / 6
    + (5 - 2 * c + 28 * t - 3 * c ** 2 + 8 * ep2 + 24 * t ** 2) * d ** 5 / 120) / Math.cos(phi);
  if (!Number.isFinite(lon) || !Number.isFinite(lat) || Math.abs(lat) >= 85.05112878 * Math.PI / 180) return null;
  return [a * lon, a * Math.log(Math.tan(Math.PI / 4 + lat / 2))];
}

function prepareMapGeometry(geom) {
  if (geom.map_bounds) return;
  const match = /^EPSG:(32[67](0[1-9]|[1-5][0-9]|60))$/i.exec(geom.crs || '');
  if (!match || !geom.nodes.length) return;
  const epsg = Number(match[1]), zone = epsg % 100;
  const points = geom.nodes.map((node) => projectUtm(node.x, node.y, zone, epsg >= 32700));
  if (points.some((point) => !point)) return;
  const bounds = [Infinity, Infinity, -Infinity, -Infinity];
  geom.nodes.forEach((node, i) => { [node.mx, node.my] = points[i]; });
  for (const [mx, my] of points) {
    bounds[0] = Math.min(bounds[0], mx); bounds[1] = Math.min(bounds[1], my);
    bounds[2] = Math.max(bounds[2], mx); bounds[3] = Math.max(bounds[3], my);
  }
  geom.map_bounds = bounds;
}

const LAYERS = {
  voltage: { title: 'Voltage level (%)', bins: [
    [-Infinity, 90, '#a00000', 3, '< 90'], [90, 95, '#ff9900', 2.5, '90 - 95'], [95, 105, '#1f4fd6', 1.6, '95 - 105'],
    [105, 110, '#cc00cc', 2.5, '105 - 110'], [110, Infinity, '#ff0000', 3, '> 110']] },
  loading: { title: 'Loading level color(%)', bins: [
    [0, 80, '#0000ff', 1, '0 - 80'], [80, 90, '#008000', 2, '80 - 90'], [90, 95, '#ffff00', 3, '90 - 95'],
    [95, 105, '#ff8040', 4, '95 - 105'], [105, 150, '#ff0000', 5, '105 - 150'], [150, Infinity, '#800000', 5, '> 150']] },
  phase: { title: 'Phase', bins: [['A', '#e00000'], ['B', '#00a000'], ['C', '#0000e0'], ['AB', '#b07000'],
    ['AC', '#a000a0'], ['BC', '#008080'], ['ABC', '#202020']] },
};

/* ---------------- API ---------------- */
async function api(path, body) {
  const res = await fetch('/api/' + path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {});
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function status(text) { $('#status').textContent = text; }
function busy(on, text = 'Running...') { $('#busy').hidden = !on; $('#busyText').textContent = text; }

/* ---------------- Carga ---------------- */
async function init() {
  const { files, initial } = await api('files');
  const file = initial || files[0];
  if (file) await openFile(file);
  else status('No .nandu.sqlite files in the data folder');
}

async function openFile(file) {
  busy(true, 'Loading network...');
  try {
    const data = await api('network?file=' + encodeURIComponent(file));
    S.file = file; S.catalog = null;
    applyNetwork(data);
    for (const option of $('#mapLayer').options) if (option.value !== 'none') option.disabled = !S.geom.map_bounds;
    if (!S.geom.map_bounds) $('#mapLayer').value = 'none';
    previousMapLayer = $('#mapLayer').value;
    updateMapAttribution();
    S.selection = null;
    $('#viewTab').textContent = `${S.geom.networks.map((n) => n.id).join(', ')} - Geographic`;
    updateCrs();
    renderProperties(null); renderReports(); resize(false); fitExtents();
    status(`Ready - ${S.sections.length} sections, ${S.nodes.size} nodes`);
  } catch (e) { status('Error: ' + e.message); }
  busy(false);
}

/* Geometría, explorador y estado de edición de una respuesta network/edit (sin mover la vista). */
function applyNetwork(data) {
  S.geom = data.geometry; S.explorer = data.explorer; S.last = data.last; S.edits = data.edits || { count: 0 };
  prepareMapGeometry(S.geom);
  S.nodes = new Map(S.geom.nodes.map((n) => [n.id, n]));
  S.sections = S.geom.sections.filter((s) => S.nodes.has(s.from) && S.nodes.has(s.to));
  S.sectionById = new Map(S.sections.map((s) => [s.id, s]));
  S.sourcesByNode = new Map(S.geom.sources.map((s) => [s.node, s]));
  S.netColor = new Map(S.geom.networks.map((n) => [n.id, n.color]));
  const name = S.file.split(/[\\/]/).pop();
  $('#title').textContent = `Ñandu - [${name}${S.edits.count ? ' *' : ''}]`;
  $('#editState').textContent = S.edits.count ? `Modified (${S.edits.count} ${S.edits.count === 1 ? 'change' : 'changes'})` : '';
  renderExplorer();
}

/* ---------------- Vista ---------------- */
function resize(redraw = true) {
  const r = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, r.width * S.dpr); canvas.height = Math.max(1, r.height * S.dpr);
  mapCanvas.width = canvas.width; mapCanvas.height = canvas.height;
  if (redraw) draw();
}
const W = () => canvas.width / S.dpr;
const H = () => canvas.height / S.dpr;
const mapEnabled = () => $('#mapLayer').value !== 'none' && !!S.geom?.map_bounds;
const point = (n) => mapEnabled() ? [n.mx, n.my] : [n.x, n.y];
const screenNode = (n) => toScreen(...point(n));
function updateCrs() { $('#crs').textContent = mapEnabled() ? 'EPSG:3857' : (S.geom?.crs || ''); }
const toScreen = (x, y) => [x * S.view.s + S.view.tx, -y * S.view.s + S.view.ty];
const toWorld = (sx, sy) => [(sx - S.view.tx) / S.view.s, -(sy - S.view.ty) / S.view.s];

function fitExtents(bounds) {
  if (!S.geom) return;
  const [x0, y0, x1, y1] = bounds || (mapEnabled() ? S.geom.map_bounds : S.geom.bounds);
  const pad = 30;
  const s = Math.min((W() - 2 * pad) / Math.max(x1 - x0, 1), (H() - 2 * pad) / Math.max(y1 - y0, 1));
  S.view.s = s;
  S.view.tx = pad + (W() - 2 * pad - (x1 - x0) * s) / 2 - x0 * s;
  S.view.ty = pad + (H() - 2 * pad - (y1 - y0) * s) / 2 + y1 * s;
  draw();
}

function zoomAt(factor, sx = W() / 2, sy = H() / 2) {
  const [wx, wy] = toWorld(sx, sy);
  S.view.s *= factor;
  S.view.tx = sx - wx * S.view.s;
  S.view.ty = sy + wy * S.view.s;
  draw();
}

function sectionValue(sec) {
  const layer = $('#colorLayer').value;
  const r = S.last && S.last.sections ? S.last.sections[sec.id] : null;
  if (layer === 'loading') return r ? r.loading_pct : null;
  if (layer === 'voltage') {
    const v = S.last && S.last.nodes ? S.last.nodes[sec.to] : null;
    if (!v) return null;
    let worst = null;
    for (const p of sec.phases) if (v[p]) { const x = v[p].pct; if (worst === null || Math.abs(x - 100) > Math.abs(worst - 100)) worst = x; }
    return worst;
  }
  return null;
}

function styleFor(sec) {
  const layer = $('#colorLayer').value;
  const multi = sec.phases.length === 3;
  const base = { color: S.netColor.get(sec.network) || '#1f3a93', width: multi ? 2 : 1, dash: multi ? [] : [8, 3, 2, 3] };
  if (layer === 'phase') {
    const bin = LAYERS.phase.bins.find((b) => b[0] === sec.phases);
    return { ...base, color: bin ? bin[1] : '#555' };
  }
  if (layer === 'voltage' || layer === 'loading') {
    const v = sectionValue(sec);
    if (v === null || v === undefined) return { ...base, color: '#b0b0b0', width: 1 };
    const bin = LAYERS[layer].bins.find((b) => v > b[0] && v <= b[1]) || LAYERS[layer].bins[0];
    return { ...base, color: bin[2], width: bin[3] };
  }
  return base;
}

function isOpen(sec) {
  return sec.devices.some((d) => d.closed !== undefined && d.closed.length < sec.phases.length);
}

function drawMap() {
  mapCtx.setTransform(S.dpr, 0, 0, S.dpr, 0, 0);
  mapCtx.clearRect(0, 0, W(), H());
  if (!mapEnabled()) return;
  // Un nivel entero y escala continua: teselas y nodos usan los mismos metros EPSG:3857.
  const z = Math.max(0, Math.min(19, Math.floor(Math.log2(2 * MERCATOR_LIMIT * S.view.s / 256))));
  const count = 2 ** z;
  const metres = 2 * MERCATOR_LIMIT / count;
  const size = metres * S.view.s;
  const [left, top] = toWorld(0, 0), [right, bottom] = toWorld(W(), H());
  const x0 = Math.max(0, Math.floor((left + MERCATOR_LIMIT) / metres));
  const x1 = Math.min(count - 1, Math.floor((right + MERCATOR_LIMIT) / metres));
  const y0 = Math.max(0, Math.floor((MERCATOR_LIMIT - top) / metres));
  const y1 = Math.min(count - 1, Math.floor((MERCATOR_LIMIT - bottom) / metres));
  if ((x1 - x0 + 1) * (y1 - y0 + 1) > 64) return;
  for (let x = x0; x <= x1; x++) for (let y = y0; y <= y1; y++) {
    const provider = $('#mapLayer').value;
    const key = `${provider}/${z}/${x}/${y}`;
    let image = tileCache.get(key);
    if (!image) {
      image = new Image();
      tileCache.set(key, image);
      image.onload = () => { if (mapEnabled()) drawMap(); };
      image.src = MAP_TILES[provider](z, x, y);
    }
    if (image.complete && image.naturalWidth) {
      const [sx, sy] = toScreen(-MERCATOR_LIMIT + x * metres, MERCATOR_LIMIT - y * metres);
      mapCtx.drawImage(image, sx, sy, size, size);
    }
  }
  // Limitar memoria sin eliminar la tesela visible mientras se pinta.
  while (tileCache.size > 256) tileCache.delete(tileCache.keys().next().value);
  mapCtx.fillStyle = $('#mapLayer').value === 'satellite' ? 'rgba(255,255,255,.25)' : 'rgba(255,255,255,.18)';
  mapCtx.fillRect(0, 0, W(), H());
}

function draw() {
  drawMap();
  ctx.setTransform(S.dpr, 0, 0, S.dpr, 0, 0);
  ctx.clearRect(0, 0, W(), H());
  if (!S.geom) return;
  const sel = S.selection;
  // Selección debajo, como el halo de CYME.
  if (sel && sel.kind === 'section' && S.sectionById.has(sel.id)) {
    const sec = S.sectionById.get(sel.id);
    const a = screenNode(S.nodes.get(sec.from));
    const b = screenNode(S.nodes.get(sec.to));
    ctx.strokeStyle = 'rgba(51,153,255,.45)'; ctx.lineWidth = 9; ctx.setLineDash([]); ctx.lineCap = 'round';
    ctx.beginPath(); ctx.moveTo(...a); ctx.lineTo(...b); ctx.stroke();
  }
  ctx.lineCap = 'butt';
  for (const sec of S.sections) {
    const st = styleFor(sec);
    const a = screenNode(S.nodes.get(sec.from));
    const b = screenNode(S.nodes.get(sec.to));
    if (Math.max(a[0], b[0]) < -20 || Math.min(a[0], b[0]) > W() + 20 || Math.max(a[1], b[1]) < -20 || Math.min(a[1], b[1]) > H() + 20) continue;
    ctx.strokeStyle = st.color; ctx.lineWidth = st.width; ctx.setLineDash(st.dash);
    ctx.beginPath(); ctx.moveTo(...a); ctx.lineTo(...b); ctx.stroke();
  }
  ctx.setLineDash([]);
  const zoomed = S.view.s > 0.08;
  if ($('#showDevices').checked) drawDevices(zoomed);
  if (zoomed) drawNodes();
  drawSources();
  if (sel && sel.kind === 'node' && S.nodes.has(sel.id)) {
    const n = S.nodes.get(sel.id); const [x, y] = screenNode(n);
    ctx.fillStyle = '#1e88e5';
    for (const [dx, dy] of [[-7, -7], [7, -7], [-7, 7], [7, 7], [0, -7], [0, 7], [-7, 0], [7, 0]]) ctx.fillRect(x + dx - 1.5, y + dy - 1.5, 3, 3);
  }
  drawTags();
  drawPending();
  renderLegend();
}

function drawNodes() {
  ctx.fillStyle = '#1f2f6b';
  for (const n of S.nodes.values()) {
    const [x, y] = screenNode(n);
    if (x < -5 || y < -5 || x > W() + 5 || y > H() + 5) continue;
    ctx.beginPath(); ctx.arc(x, y, 2.2, 0, 2 * Math.PI); ctx.fill();
  }
}

function devicePoint(sec, loc, a, b) {
  const len = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
  const off = Math.min(14, len * 0.3) / len;
  const t = loc === 'From' ? off : loc === 'Middle' ? 0.5 : 1 - off;
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, Math.atan2(b[1] - a[1], b[0] - a[0]), len];
}

function drawDevices(zoomed) {
  for (const sec of S.sections) {
    const a = screenNode(S.nodes.get(sec.from));
    const b = screenNode(S.nodes.get(sec.to));
    if (Math.max(a[0], b[0]) < 0 || Math.min(a[0], b[0]) > W() || Math.max(a[1], b[1]) < 0 || Math.min(a[1], b[1]) > H()) continue;
    for (const d of sec.devices) {
      const major = ['Regulator', 'Recloser', 'Breaker', 'ShuntCapacitor'].includes(d.type);
      if (!zoomed && !major) continue;
      const [x, y, ang, len] = devicePoint(sec, d.location, a, b);
      if (len < 18 && !major) continue;
      symbol(d, x, y, ang, sec);
    }
  }
}

function symbol(d, x, y, ang, sec) {
  const c = styleFor(sec).color;
  ctx.save(); ctx.translate(x, y); ctx.lineWidth = 1.2; ctx.strokeStyle = c; ctx.fillStyle = '#fff';
  switch (d.type) {
    case 'Regulator':
      ctx.strokeStyle = '#0b3d91'; ctx.beginPath(); ctx.arc(0, 0, 7, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(-5, 5); ctx.lineTo(5, -5); ctx.moveTo(1, -5); ctx.lineTo(5, -5); ctx.lineTo(5, -1); ctx.stroke();
      break;
    case 'Recloser':
      ctx.rotate(ang); ctx.strokeStyle = '#0b3d91'; ctx.fillRect(-6, -6, 12, 12); ctx.strokeRect(-6, -6, 12, 12);
      ctx.rotate(-ang); ctx.fillStyle = '#0b3d91'; ctx.font = 'bold 9px Segoe UI'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText('R', 0, 0.5);
      break;
    case 'Breaker':
      ctx.rotate(ang); ctx.fillStyle = (d.closed || '').length ? '#c00000' : '#2e7d32'; ctx.fillRect(-6, -6, 12, 12);
      ctx.strokeStyle = '#000'; ctx.strokeRect(-6, -6, 12, 12);
      break;
    case 'Switch': {
      ctx.rotate(ang); const closed = (d.closed || '').length === sec.phases.length;
      ctx.strokeStyle = '#000'; ctx.fillStyle = closed ? '#000' : '#fff';
      ctx.fillRect(-4, -4, 8, 8); ctx.strokeRect(-4, -4, 8, 8);
      break; }
    case 'Fuse':
      ctx.rotate(ang); ctx.strokeStyle = '#000'; ctx.fillRect(-6, -3, 12, 6); ctx.strokeRect(-6, -3, 12, 6);
      ctx.beginPath(); ctx.moveTo(-6, 0); ctx.lineTo(6, 0); ctx.stroke();
      break;
    case 'ShuntCapacitor':
      ctx.rotate(ang + Math.PI / 2); ctx.strokeStyle = '#006400'; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(0, 6); ctx.moveTo(-6, 6); ctx.lineTo(6, 6); ctx.moveTo(-6, 10); ctx.lineTo(6, 10); ctx.stroke();
      break;
    case 'SpotLoad':
      // rotate(ang) deja el eje y local perpendicular al tramo: la carga cuelga de la línea.
      ctx.rotate(ang); ctx.fillStyle = c; ctx.lineWidth = 1.6;
      ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(0, 9); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(-5.5, 9); ctx.lineTo(5.5, 9); ctx.lineTo(0, 17); ctx.closePath(); ctx.fill();
      break;
    default: break;
  }
  ctx.restore();
}

function drawSources() {
  for (const src of S.geom.sources) {
    const n = S.nodes.get(src.node); if (!n) continue;
    const [x, y] = screenNode(n);
    ctx.save(); ctx.translate(x, y - 16);
    ctx.strokeStyle = '#0b3d91'; ctx.fillStyle = '#fff'; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.moveTo(0, 9); ctx.lineTo(0, 16); ctx.stroke();
    ctx.beginPath(); ctx.arc(0, 0, 9, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(-5, 0); ctx.bezierCurveTo(-3, -6, -1, -6, 0, 0); ctx.bezierCurveTo(1, 6, 3, 6, 5, 0); ctx.stroke();
    ctx.fillStyle = '#0b3d91'; ctx.font = '600 11px Segoe UI'; ctx.textAlign = 'left'; ctx.fillText(src.id, 12, 4);
    ctx.restore();
  }
}

function drawTags() {
  const layer = $('#tagLayer').value;
  if (layer === 'none') return;
  const boxes = [];
  if (layer === 'voltage' || layer === 'id') {
    for (const n of S.nodes.values()) {
      const [x, y] = screenNode(n);
      if (x < 0 || y < 0 || x > W() || y > H()) continue;
      const lines = [n.id];
      if (layer === 'voltage') {
        const v = S.last && S.last.nodes ? S.last.nodes[n.id] : null;
        if (!v) continue;
        for (const p of 'ABC') if (v[p]) lines.push(`V${p.toLowerCase()} = ${fmt(v[p].kv * 1000, 0)} V`);
      }
      boxes.push([x + 8, y - 8, lines]);
    }
  } else if (layer === 'current' && S.last && S.last.sections) {
    for (const sec of S.sections) {
      const r = S.last.sections[sec.id]; if (!r) continue;
      const a = screenNode(S.nodes.get(sec.from));
      const b = screenNode(S.nodes.get(sec.to));
      const x = (a[0] + b[0]) / 2, y = (a[1] + b[1]) / 2;
      if (x < 0 || y < 0 || x > W() || y > H()) continue;
      boxes.push([x + 6, y + 6, [sec.id, ...Object.entries(r.current_a).map(([p, v]) => `I${p.toLowerCase()} = ${fmt(v, 1)} A`)]]);
    }
  }
  if (boxes.length > 250) { status(`Zoom in to display ${boxes.length} tags`); return; }
  ctx.font = '11px Segoe UI'; ctx.textBaseline = 'top';
  for (const [x, y, lines] of boxes) {
    const w = Math.max(...lines.map((l) => ctx.measureText(l).width)) + 8;
    const h = lines.length * 13 + 4;
    ctx.fillStyle = 'rgba(255,255,255,.92)'; ctx.fillRect(x, y, w, h);
    ctx.strokeStyle = '#333'; ctx.lineWidth = 1; ctx.strokeRect(x + .5, y + .5, w, h);
    ctx.fillStyle = '#000'; ctx.textAlign = 'center';
    lines.forEach((l, i) => ctx.fillText(l, x + w / 2, y + 3 + i * 13));
  }
  ctx.textAlign = 'left';
}

function renderLegend() {
  const layer = $('#colorLayer').value;
  const el = $('#legend');
  if (layer === 'network') {
    el.hidden = false;
    el.innerHTML = '<div class="title">Network color</div>' + S.geom.networks.map((n) =>
      `<div class="row"><i style="border-top:3px solid ${n.color}"></i>${esc(n.id)}</div>`).join('');
    return;
  }
  const def = LAYERS[layer];
  el.hidden = false;
  el.innerHTML = `<div class="title">${def.title}</div>` + def.bins.map((b) => layer === 'phase'
    ? `<div class="row"><i style="border-top:3px solid ${b[1]}"></i>${b[0]}</div>`
    : `<div class="row"><i style="border-top:${b[3]}px solid ${b[2]}"></i>${b[4]}</div>`).join('')
    + ((layer !== 'phase' && !(S.last && S.last.nodes)) ? '<div class="note">Run Load Flow first</div>' : '');
}

/* ---------------- Interacción ---------------- */
function hitTest(sx, sy) {
  let best = null;
  for (const n of S.nodes.values()) {
    const [x, y] = screenNode(n);
    const d = Math.hypot(x - sx, y - sy);
    if (d < 6 && (!best || d < best.d)) best = { kind: 'node', id: n.id, d };
  }
  if (best) return best;
  for (const sec of S.sections) {
    const a = screenNode(S.nodes.get(sec.from));
    const b = screenNode(S.nodes.get(sec.to));
    const vx = b[0] - a[0], vy = b[1] - a[1];
    const t = Math.max(0, Math.min(1, ((sx - a[0]) * vx + (sy - a[1]) * vy) / (vx * vx + vy * vy || 1)));
    const d = Math.hypot(a[0] + t * vx - sx, a[1] + t * vy - sy);
    if (d < 5 && (!best || d < best.d)) best = { kind: 'section', id: sec.id, d };
  }
  return best;
}

let drag = null;
canvas.addEventListener('mousedown', (e) => {
  if (e.button !== 0) return;
  hideContextMenu();
  const hit = S.edit && !S.pending ? hitTest(e.offsetX, e.offsetY) : null;
  // En modo edición, arrastrar un nodo lo mueve; en otro caso se desplaza la vista.
  const node = hit && hit.kind === 'node' ? S.nodes.get(hit.id) : null;
  drag = { x: e.offsetX, y: e.offsetY, tx: S.view.tx, ty: S.view.ty, moved: false, node, start: node && point(node) };
});
window.addEventListener('mouseup', (e) => {
  if (drag && drag.moved && drag.node) finishNodeDrag(drag);
  else if (drag && !drag.moved && e.target === canvas) {
    if (S.pending) pickSectionEnd(e.offsetX, e.offsetY);
    else select(hitTest(e.offsetX, e.offsetY));
  }
  drag = null; canvas.classList.remove('panning');
});
canvas.addEventListener('mousemove', (e) => {
  const [wx, wy] = toWorld(e.offsetX, e.offsetY);
  $('#coords').textContent = `X: ${wx.toFixed(2)}  Y: ${wy.toFixed(2)}`;
  if (S.pending) { S.pending.mouse = [e.offsetX, e.offsetY]; draw(); }
  if (drag) {
    const dx = e.offsetX - drag.x, dy = e.offsetY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) { drag.moved = true; if (!drag.node) canvas.classList.add('panning'); }
    if (drag.moved && drag.node) { setPoint(drag.node, toWorld(e.offsetX, e.offsetY)); draw(); }
    else if (drag.moved) { S.view.tx = drag.tx + dx; S.view.ty = drag.ty + dy; draw(); }
    return;
  }
  tooltip(hitTest(e.offsetX, e.offsetY), e.offsetX, e.offsetY);
});
canvas.addEventListener('contextmenu', (e) => {
  if (!S.edit) return;
  e.preventDefault();
  if (S.pending) { cancelPending(); return; }
  const hit = hitTest(e.offsetX, e.offsetY);
  if (hit) { select(hit); showContextMenu(hit, e.clientX, e.clientY); }
});
canvas.addEventListener('mouseleave', () => { $('#tooltip').hidden = true; });
canvas.addEventListener('wheel', (e) => { e.preventDefault(); zoomAt(e.deltaY < 0 ? 1.25 : 0.8, e.offsetX, e.offsetY); }, { passive: false });
canvas.addEventListener('dblclick', (e) => { const h = hitTest(e.offsetX, e.offsetY); if (h) { select(h); zoomTo(h); } });

function tooltip(hit, x, y) {
  const el = $('#tooltip');
  if (!hit) { el.hidden = true; return; }
  let text = hit.id;
  if (hit.kind === 'node') {
    const v = S.last && S.last.nodes ? S.last.nodes[hit.id] : null;
    if (v) for (const p of 'ABC') if (v[p]) text += `\nV${p}: ${fmt(v[p].kv, 3)} kV  ${fmt(v[p].pct, 2)} %`;
  } else {
    const sec = S.sectionById.get(hit.id);
    text += `  [${sec.phases}]\n` + sec.devices.map((d) => `${d.type} ${d.number}`).join('\n');
    const r = S.last && S.last.sections ? S.last.sections[hit.id] : null;
    if (r) text += '\n' + Object.entries(r.current_a).map(([p, v]) => `I${p}: ${fmt(v, 1)} A`).join('  ')
      + (r.loading_pct !== null ? `\nLoading: ${fmt(r.loading_pct, 1)} %` : '');
  }
  el.textContent = text; el.hidden = false;
  el.style.left = (x + 14) + 'px'; el.style.top = (y + 14) + 'px';
}

function zoomTo(hit) {
  let x0, y0, x1, y1;
  if (hit.kind === 'node') { const [x, y] = point(S.nodes.get(hit.id)); [x0, y0, x1, y1] = [x - 150, y - 150, x + 150, y + 150]; }
  else {
    const sec = S.sectionById.get(hit.id); const a = S.nodes.get(sec.from), b = S.nodes.get(sec.to);
    const [ax, ay] = point(a), [bx, by] = point(b);
    [x0, y0, x1, y1] = [Math.min(ax, bx) - 150, Math.min(ay, by) - 150, Math.max(ax, bx) + 150, Math.max(ay, by) + 150];
  }
  fitExtents([x0, y0, x1, y1]);
}

async function select(hit) {
  S.selection = hit ? { kind: hit.kind, id: hit.id } : null;
  draw();
  if (S.last && S.last.profile && (S.report === 'profile' || S.popouts.has('profile'))) renderReports();
  $$('.tree .node.selected').forEach((el) => el.classList.remove('selected'));
  $('#selCount').textContent = hit ? `${hit.kind === 'node' ? 'Node' : 'Section'}: ${hit.id}` : '';
  if (!hit) { renderProperties(null); return; }
  try { renderProperties(await api(`properties?file=${encodeURIComponent(S.file)}&kind=${hit.kind}&id=${encodeURIComponent(hit.id)}`)); }
  catch (e) { status('Error: ' + e.message); }
}

/* ---------------- Explorador ---------------- */
const GROUP_ORDER = ['Source', 'Regulator', 'Breaker', 'Recloser', 'Fuse', 'Switch', 'ShuntCapacitor'];
const GROUP_LABEL = { Source: 'Sources', Regulator: 'Regulators', Breaker: 'Breakers', Recloser: 'Reclosers', Fuse: 'Fuses', Switch: 'Switches', ShuntCapacitor: 'Shunt Capacitors' };
const GROUP_COLOR = { Source: '#0b3d91', Regulator: '#0b3d91', Breaker: '#c00000', Recloser: '#0b3d91', Fuse: '#555', Switch: '#000', ShuntCapacitor: '#006400' };

function renderExplorer() {
  const html = ['<ul class="tree root">'];
  for (const net of S.explorer) {
    const color = S.netColor.get(net.network);
    html.push(`<li><div class="node"><span class="twisty">▾</span><i class="ico" style="background:${color}"></i><b>${esc(net.network)}</b>
      <span class="count">(${(net.counts.OverheadLine || 0) + (net.counts.OverheadLineUnbalanced || 0) + (net.counts.Underground || 0)} lines, ${net.counts.SpotLoad || 0} loads)</span></div><ul class="tree">`);
    for (const g of GROUP_ORDER) {
      const items = net.groups[g]; if (!items || !items.length) continue;
      html.push(`<li class="${items.length > 12 ? 'collapsed' : ''}"><div class="node"><span class="twisty">${items.length > 12 ? '▸' : '▾'}</span>
        <i class="ico" style="background:${GROUP_COLOR[g]}"></i>${GROUP_LABEL[g]} <span class="count">(${items.length})</span></div><ul class="tree">`);
      for (const it of items) {
        const target = it.section ? `data-kind="section" data-id="${esc(it.section)}"` : `data-kind="node" data-id="${esc(it.node)}"`;
        html.push(`<li><div class="node leaf" ${target}>${esc(it.number)}</div></li>`);
      }
      html.push('</ul></li>');
    }
    html.push('</ul></li>');
  }
  html.push('</ul>');
  $('#explorerBody').innerHTML = html.join('');
}

$('#explorerBody').addEventListener('click', (e) => {
  const node = e.target.closest('.node'); if (!node) return;
  if (node.classList.contains('leaf')) {
    const hit = { kind: node.dataset.kind, id: node.dataset.id };
    select(hit).then(() => node.classList.add('selected')); zoomTo(hit); return;
  }
  const li = node.parentElement; li.classList.toggle('collapsed');
  const tw = $('.twisty', node); if (tw) tw.textContent = li.classList.contains('collapsed') ? '▸' : '▾';
});

/* ---------------- Propiedades ---------------- */
function renderProperties(data) {
  S.props = data;
  $('#propSelect').textContent = data ? data.title : 'No selection';
  if (!data) { $('#propBody').innerHTML = ''; return; }
  $('#propBody').innerHTML = data.groups.map((g, gi) => {
    const editable = S.edit && g.fields.some((f) => f[2]);
    const rows = g.fields.map(([k, v, meta], fi) => `<tr><td>${esc(k)}</td><td>${editable && meta ? editor(meta, gi, fi) : esc(v === null ? '' : v)}</td></tr>`).join('');
    return `<div class="pgroup ${g.collapsed ? 'collapsed' : ''} ${g.name === 'Load Flow' ? 'results' : ''} ${editable ? 'editing' : ''}" data-g="${gi}">
      <div class="phead">${esc(g.name)}</div><table>${rows}</table>
      ${editable ? '<div class="papply"><button data-papply>Apply</button><button data-preset>Reset</button></div>' : ''}</div>`;
  }).join('');
}

/* Control de un campo editable: lista si el dominio es conocido (códigos verificados o catálogo). */
function editor(meta, gi, fi) {
  const raw = meta.raw === null || meta.raw === undefined ? '' : meta.raw;
  const id = `data-f="${gi}:${fi}"`;
  if (meta.options) {
    const opts = meta.options.some(([v]) => v === meta.raw) ? meta.options : [[meta.raw, `${raw} (current)`], ...meta.options];
    return `<select class="pedit" ${id}>${opts.map(([v, t]) => `<option value="${esc(JSON.stringify(v))}" ${v === meta.raw ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select>`;
  }
  return `<input class="pedit" ${id} value="${esc(raw)}">`;
}

async function applyProperties(group) {
  const g = S.props.groups[Number(group.dataset.g)];
  const targets = new Map();
  for (const el of $$('.pedit', group)) {
    const [, fi] = el.dataset.f.split(':').map(Number);
    const [, , meta] = g.fields[fi];
    const value = el.tagName === 'SELECT' ? JSON.parse(el.value) : el.value;
    const current = meta.raw === null || meta.raw === undefined ? '' : meta.raw;
    if (el.tagName === 'SELECT' ? value === meta.raw : String(value) === String(current)) continue;
    const key = `${meta.table}|${JSON.stringify(meta.key)}`;
    if (!targets.has(key)) targets.set(key, { op: 'set', table: meta.table, key: meta.key, changes: {} });
    targets.get(key).changes[meta.field] = value;
  }
  if (!targets.size) { status('No changes'); return; }
  await edit({ op: 'batch', ops: [...targets.values()] });
}

$('#propBody').addEventListener('click', (e) => {
  const group = e.target.closest('.pgroup');
  if (e.target.closest('[data-papply]')) { applyProperties(group); return; }
  if (e.target.closest('[data-preset]')) { renderProperties(S.props); return; }
  const h = e.target.closest('.phead'); if (h) h.parentElement.classList.toggle('collapsed');
});
$('#propBody').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && e.target.classList.contains('pedit')) { e.preventDefault(); applyProperties(e.target.closest('.pgroup')); }
});

/* ---------------- Informes ---------------- */
const REPORTS = [
  ['messages', 'Messages'], ['summary', 'Summary Report'], ['abnormal', 'Abnormal conditions'], ['buses', 'Buses'],
  ['lines', 'Lines and Cables'], ['regulators', 'Regulators'], ['capacitors', 'Shunt Capacitors'],
  ['profile', 'Voltage Profile'], ['iterations', 'Iteration Report'], ['meters', 'DSE - Meter'], ['loads', 'DSE - Load Values'],
];
const REPORT_LABEL = Object.fromEntries(REPORTS);
const PREFIX = { loadflow: 'Load Flow', allocate: 'Load Allocation', estimate: 'Distribution State Estimator' };
const GRID_LIMIT = 3000;
// Colores de fase del unifilar (convención CYME). Rojo/verde no se distinguen con deuteranopía:
// en el perfil cada fase lleva además su propio trazo y etiqueta directa.
const PHASE_STYLE = { A: { color: '#e00000', dash: '' }, B: { color: '#00a000', dash: '7 4' }, C: { color: '#0000e0', dash: '2 3' } };

function reportAvailable(key, kind) {
  if (key === 'meters') return kind === 'estimate';
  if (key === 'loads') return ['estimate', 'allocate'].includes(kind);
  return true;
}
function reportLabel(key) {
  return key === 'loads' && S.last && S.last.analysis === 'allocate' ? 'Load Allocation - Detailed' : REPORT_LABEL[key];
}
const limits = () => (S.last && S.last.abnormal ? S.last.abnormal.limits_pct : [95, 105]);
const outPct = (v) => v !== null && v !== undefined && (v < limits()[0] || v > limits()[1]);

/* Informes tabulares: columnas con formato, orden, filtro y exportación de los valores sin formato. */
const C = (label, d, extra = {}) => ({ label, d, ...extra });
const GRIDS = {
  buses: (L, prefix) => ({
    title: `${prefix} - Buses`,
    cols: [C('Node Id'), C('Network'), C('VA (kVLN)', 3), C('VB (kVLN)', 3), C('VC (kVLN)', 3),
      C('VA (%)', 2, { warn: outPct }), C('VB (%)', 2, { warn: outPct }), C('VC (%)', 2, { warn: outPct }),
      C('Angle A (°)', 2, { hide: true }), C('Angle B (°)', 2, { hide: true }), C('Angle C (°)', 2, { hide: true }),
      C('Distance (km)', 3, { hide: true })],
    rows: Object.entries(L.nodes || {}).map(([id, v]) => ({ kind: 'node', id, v: [id, S.nodes.get(id)?.network ?? '',
      ...[...'ABC'].map((p) => v[p]?.kv ?? null), ...[...'ABC'].map((p) => v[p]?.pct ?? null),
      ...[...'ABC'].map((p) => v[p]?.angle ?? null), L.profile?.[id] ? L.profile[id].distance_m / 1000 : null] })),
  }),
  lines: (L, prefix) => ({
    title: `${prefix} - Lines and Cables`,
    cols: [C('Section Id'), C('IA (A)', 1), C('IB (A)', 1), C('IC (A)', 1), C('Loading (%)', 1, { warn: (v) => v > 100 }),
      C('Loading A (%)', 1, { hide: true, warn: (v) => v > 100 }), C('Loading B (%)', 1, { hide: true, warn: (v) => v > 100 }),
      C('Loading C (%)', 1, { hide: true, warn: (v) => v > 100 }),
      C('Thru Power (kW)', 1), C('Thru Power (kvar)', 1), C('Total Loss (kW)', 3)],
    rows: Object.entries(L.sections || {}).map(([id, r]) => ({ kind: 'section', id, v: [id,
      ...[...'ABC'].map((p) => r.current_a[p] ?? null), r.loading_pct,
      ...[...'ABC'].map((p) => r.loading_pct_phase?.[p] ?? null), r.p_kw, r.q_kvar, r.loss_kw] })),
  }),
  regulators: (L, prefix) => ({
    title: `${prefix} - Regulators`,
    cols: [C('Regulator Id'), C('Section Id'), C('Network'), C('Equipment'), C('Connection'), C('Tap Mode'), C('Phase'),
      C('Tap', 0), C('Max Tap', 0, { hide: true }), C('V in (kVLN)', 3), C('V out (kVLN)', 3),
      C('V out (%)', 2, { warn: outPct }), C('I (A)', 1), C('I out (A)', 1, { hide: true }),
      C('Control V (120 V)', 2), C('Set V', 2), C('Bandwidth V', 2),
      C('Input Node', undefined, { hide: true }), C('Output Node', undefined, { hide: true })],
    rows: (L.regulators || []).map((r) => ({ kind: 'section', id: r.section_id, v: [r.id, r.section_id, r.network,
      r.equipment, r.connection, r.mode, r.phase, r.tap, r.max_tap, r.v_in_kv, r.v_out_kv, r.v_out_pct, r.i_a,
      r.i_out_a, r.control_v, r.set_v, r.bandwidth_v, r.input_node, r.output_node] })),
  }),
  capacitors: (L, prefix) => ({
    title: `${prefix} - Shunt Capacitors`,
    cols: [C('Capacitor Id'), C('Section Id'), C('Network'), C('Node Id'), C('Connection'), C('Phase'),
      C('Rated (kvar)', 1), C('Rated (kV)', 3), C('V (kV)', 3), C('V (%)', 2, { warn: outPct }), C('Delivered (kvar)', 1),
      C('Loading (%)', 1, { warn: (v) => v > 100 })],
    rows: (L.capacitors || []).map((r) => ({ kind: 'section', id: r.section_id, v: [r.id, r.section_id, r.network, r.node,
      r.connection, r.phase, r.rated_kvar, r.rated_kv, r.v_kv, r.v_pct, r.kvar, r.loading_pct] })),
  }),
  loads: (L) => ({
    title: L.analysis === 'allocate' ? `Load Allocation - Detailed (${L.method})` : 'Distribution State Estimator - Load Values',
    cols: [C('Load'), C('Network'), C('Initial kW', 2), C('Initial kvar', 2),
      C(L.analysis === 'allocate' ? 'Allocated kW' : 'Estimated kW', 2), C(L.analysis === 'allocate' ? 'Allocated kvar' : 'Estimated kvar', 2),
      C('Change (%)', 1)],
    rows: (L.loads || []).map((r) => ({ v: [r.id, r.network, r.initial_kw, r.initial_kvar, r.estimated_kw, r.estimated_kvar,
      r.initial_kw ? 100 * (r.estimated_kw - r.initial_kw) / r.initial_kw : null] })),
  }),
  profile: (L, prefix) => ({
    title: `${prefix} - Voltage Profile`,
    cols: [C('Node Id'), C('Network'), C('Distance (km)', 3), C('VA (%)', 2, { warn: outPct }), C('VB (%)', 2, { warn: outPct }),
      C('VC (%)', 2, { warn: outPct }), C('Upstream Node', undefined, { hide: true }), C('Upstream Section', undefined, { hide: true })],
    rows: Object.entries(L.profile || {}).map(([id, d]) => ({ kind: 'node', id, v: [id, S.nodes.get(id)?.network ?? '',
      d.distance_m / 1000, ...[...'ABC'].map((p) => L.nodes[id]?.[p]?.pct ?? null), d.parent ?? '', d.section ?? ''] })),
  }),
};

function gridState(key, spec) {
  if (!S.grids[key]) S.grids[key] = { sort: null, filter: '', hidden: new Set(spec.cols.flatMap((c, i) => (c.hide ? [i] : []))) };
  return S.grids[key];
}

function compareCells(a, b) {
  const na = a === null || a === undefined || a === '', nb = b === null || b === undefined || b === '';
  if (na || nb) return na - nb;  // vacíos al final en ambos sentidos
  return typeof a === 'number' && typeof b === 'number' ? a - b : String(a).localeCompare(String(b), 'en', { numeric: true });
}

function gridRows(spec, st) {
  const q = st.filter.trim().toLowerCase();
  let rows = q ? spec.rows.filter((r) => r.v.some((c) => c !== null && String(c).toLowerCase().includes(q))) : spec.rows.slice();
  if (st.sort) {
    const [i, dir] = st.sort;
    rows.sort((a, b) => {
      const empty = (v) => v === null || v === undefined || v === '';
      if (empty(a.v[i]) || empty(b.v[i])) return compareCells(a.v[i], b.v[i]);
      return dir * compareCells(a.v[i], b.v[i]);
    });
  }
  return rows;
}

const cellText = (c, v) => (c.d === undefined ? esc(v) : fmt(v, c.d));

function gridTable(spec, st) {
  const cols = spec.cols.map((c, i) => [c, i]).filter(([, i]) => !st.hidden.has(i));
  const rows = gridRows(spec, st);
  const arrow = (i) => (st.sort && st.sort[0] === i ? (st.sort[1] > 0 ? ' ▲' : ' ▼') : '');
  return `<div class="rep-count">${rows.length} of ${spec.rows.length} rows${rows.length > GRID_LIMIT ? ` (showing first ${GRID_LIMIT}; export includes all)` : ''}</div>
    <table class="rep grid"><thead><tr>${cols.map(([c, i]) => `<th class="sortable ${c.d !== undefined ? 'num' : ''}" data-col="${i}" title="Sort">${esc(c.label)}${arrow(i)}</th>`).join('')}</tr></thead>
    <tbody>${rows.slice(0, GRID_LIMIT).map((r) => `<tr class="${r.kind ? 'clickable' : ''}" ${r.kind ? `data-kind="${r.kind}" data-id="${esc(r.id)}"` : ''}>${cols.map(([c, i]) =>
      `<td class="${c.d !== undefined ? 'num' : ''} ${c.warn && r.v[i] !== null && c.warn(r.v[i]) ? 'out' : ''}">${cellText(c, r.v[i])}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}

/* Informes con formato propio (no tabulares). */
const HTML_REPORTS = {
  messages: (L) => `<table class="rep msgs"><tr><th>Level</th><th>Message</th></tr>${L.messages.map((m) => `<tr class="msg ${m.level}"><td>${m.level}</td><td>${esc(m.text)}</td></tr>`).join('')}</table>`,
  summary: (L, prefix) => !L.summary ? '<p class="empty">No converged results.</p>' : Object.entries(L.summary).map(([net, rows]) =>
    `<div class="rep-title">${prefix} - Summary Report: ${esc(net)}</div><table class="rep"><tr><th>Total Summary</th><th class="num">kW</th><th class="num">kvar</th><th class="num">kVA</th><th class="num">PF(%)</th></tr>
    ${rows.map((r) => `<tr class="${r.item.startsWith('Total') ? 'total' : ''}"><td>${esc(r.item)}</td><td class="num">${fmt(r.kw)}</td><td class="num">${fmt(r.kvar)}</td><td class="num">${fmt(r.kva)}</td><td class="num">${fmt(r.pf)}</td></tr>`).join('')}</table>`).join(''),
  abnormal: (L, prefix) => !L.abnormal ? '<p class="empty">No converged results.</p>' : `<div class="rep-title">${prefix} - Abnormal Conditions</div>
    <table class="rep"><tr><th>Condition</th><th>Phase</th><th class="num">Count</th><th>Worst Condition</th><th class="num">Value (%)</th></tr>
    ${L.abnormal.rows.map((r) => `<tr class="clickable" data-kind="${r.condition === 'Overload' ? 'section' : 'node'}" data-id="${esc(r.worst)}"><td>${r.condition}</td><td>${r.phase}</td><td class="num">${r.count}</td><td>${esc(r.worst)}</td><td class="num">${r.value === null ? '' : fmt(r.value)}</td></tr>`).join('')}</table>
    <p>Buses outside limits (${L.abnormal.limits_pct[0]} - ${L.abnormal.limits_pct[1]} %): <b>${L.abnormal.buses_outside_limits}</b></p>`,
  iterations: (L) => `<div class="rep-title">Iteration Report</div><table class="rep"><tr><th class="num">Iteration</th><th class="num">Max dV (pu)</th><th>Detail</th></tr>
    ${L.iterations.map((it) => `<tr><td class="num">${it.iteration}</td><td class="num">${Number(it.max_dv_pu).toExponential(3)}</td><td>${it.mismatch_pct !== undefined ? 'Meter mismatch ' + fmt(it.mismatch_pct, 3) + ' %' : it.max_current_mismatch_a !== undefined ? 'Mismatch ' + Number(it.max_current_mismatch_a).toExponential(2) + ' A' : 'J = ' + fmt(it.objective_before_step, 4)}</td></tr>`).join('')}</table>`,
  meters: (L) => {
    if (!L.meter_residuals) return '<p class="empty">Run the Distribution State Estimator.</p>';
    const qi = L.quality_indices;
    const level = (v) => !qi || v === undefined ? '' : v > qi.normalized_residual_levels[1] ? 'out' : v > qi.normalized_residual_levels[0] ? 'warnv' : '';
    return `<div class="rep-title">Distribution State Estimator - Meter</div>
    ${qi ? `<table class="rep"><tr><th>Solution Quality Indices</th><th class="num">Value</th><th>Result</th></tr>
      <tr><td>Solution cost J</td><td class="num">${fmt(L.solution_cost, 3)}</td><td class="${qi.solution_cost_passes ? '' : 'out'}">${qi.solution_cost_passes ? 'Pass' : 'Bad data suspected'} (χ² ${fmt(100 * qi.chi2_confidence, 0)} % = ${fmt(qi.chi2_threshold, 2)}, dof ${qi.degrees_of_freedom})</td></tr>
      <tr><td>Suspicious measurements (r<sub>N</sub> &gt; ${qi.normalized_residual_levels[0]})</td><td class="num">${qi.suspicious_measurements}</td><td></td></tr>
      <tr><td>Erroneous measurements (r<sub>N</sub> &gt; ${qi.normalized_residual_levels[1]})</td><td class="num">${qi.erroneous_measurements}</td><td></td></tr></table>`
      : `<p>Solution cost J = ${fmt(L.solution_cost, 3)} (enable quality indices in the DSE dialog for χ² and normalized residuals)</p>`}
    <table class="rep"><tr><th>Meter</th><th>Phase</th><th>Quantity</th><th class="num">Measured</th><th class="num">Estimated</th><th class="num">σ</th><th class="num">Weighted residual</th><th class="num">Normalized residual</th></tr>
    ${L.meter_residuals.map((r) => `<tr><td>${esc(r.meter)}</td><td>${r.phase}</td><td>${r.quantity === 'current_a' ? 'Current (A)' : 'Power factor'}</td><td class="num">${fmt(r.measured, 3)}</td><td class="num">${fmt(r.estimated, 3)}</td><td class="num">${fmt(r.sigma, 4)}</td><td class="num">${fmt(r.weighted_residual, 2)}</td><td class="num ${level(r.normalized_residual)}">${r.normalized_residual === undefined ? '' : fmt(r.normalized_residual, 2)}</td></tr>`).join('')}</table>
    ${qi ? `<table class="rep"><tr><th>Load (largest normalized residuals)</th><th>Quantity</th><th class="num">r<sub>N</sub></th></tr>${qi.largest_load_residuals.map((r) => `<tr><td>${esc(r.load)}</td><td>${r.quantity}</td><td class="num ${level(r.normalized_residual)}">${fmt(r.normalized_residual, 2)}</td></tr>`).join('')}</table>` : ''}`;
  },
};

/* Perfil de tensión: todos los nodos (puntos) y el camino fuente → nodo seleccionado (líneas por fase). */
function profileTarget(L) {
  const sel = S.selection;
  if (sel && sel.kind === 'node' && L.profile[sel.id]) return sel.id;
  if (sel && sel.kind === 'section') { const sec = S.sectionById.get(sel.id); if (sec && L.profile[sec.to]) return sec.to; }
  let best = null;
  for (const [id, d] of Object.entries(L.profile)) if (!best || d.distance_m > L.profile[best].distance_m) best = id;
  return best;
}

function profileChart(L, size) {
  if (!L.profile || !Object.keys(L.profile).length) return '<p class="empty">No converged results.</p>';
  const target = profileTarget(L);
  const path = [];
  for (let n = target, guard = 0; n && guard < 100000; n = L.profile[n]?.parent, guard++) path.unshift(n);
  // Tamaño en píxeles reales del panel (o de la ventana desacoplada): el texto no se escala.
  const W0 = Math.max(420, size.w), H0 = Math.max(220, Math.min(700, size.h)), m = { l: 52, r: 30, t: 30, b: 38 };
  const pts = [];
  for (const [id, d] of Object.entries(L.profile)) for (const p of 'ABC') { const v = L.nodes[id]?.[p]; if (v) pts.push([d.distance_m / 1000, v.pct, p, id]); }
  const xmax = Math.max(...pts.map((q) => q[0]), 0.001);
  const [lo, hi] = limits();
  const ymin = Math.floor(Math.min(lo - 1, ...pts.map((q) => q[1]))), ymax = Math.ceil(Math.max(hi + 1, ...pts.map((q) => q[1])));
  const X = (x) => m.l + (x / xmax) * (W0 - m.l - m.r), Y = (y) => m.t + (1 - (y - ymin) / (ymax - ymin)) * (H0 - m.t - m.b);
  const ticks = (a, b, n) => { const step = niceStep((b - a) / n); const out = []; for (let t = Math.ceil(a / step) * step; t <= b + 1e-9; t += step) out.push(+t.toFixed(6)); return out; };
  const xt = ticks(0, xmax, 8), yt = ticks(ymin, ymax, 6);
  const grid = yt.map((t) => `<line x1="${m.l}" x2="${W0 - m.r}" y1="${Y(t)}" y2="${Y(t)}" class="pf-grid"/><text x="${m.l - 6}" y="${Y(t) + 4}" text-anchor="end">${t}</text>`).join('')
    + xt.map((t) => `<text x="${X(t)}" y="${H0 - m.b + 16}" text-anchor="middle">${t}</text>`).join('');
  const limitLines = [lo, hi].map((v) => `<line x1="${m.l}" x2="${W0 - m.r}" y1="${Y(v)}" y2="${Y(v)}" class="pf-limit"/><text x="${W0 - m.r}" y="${Y(v) - 4}" text-anchor="end" class="pf-muted">${v} %</text>`).join('');
  const dots = pts.map(([x, y, p]) => `<circle cx="${X(x).toFixed(1)}" cy="${Y(y).toFixed(1)}" r="1.8" fill="${PHASE_STYLE[p].color}" opacity=".35"/>`).join('');
  const lines = [...'ABC'].map((p) => {
    const seq = path.filter((n) => L.nodes[n]?.[p]).map((n) => [X(L.profile[n].distance_m / 1000), Y(L.nodes[n][p].pct)]);
    if (seq.length < 2) return '';
    const [ex, ey] = seq[seq.length - 1];
    return `<polyline points="${seq.map((q) => q.map((c) => c.toFixed(1)).join(',')).join(' ')}" fill="none" stroke="${PHASE_STYLE[p].color}" stroke-width="2" stroke-dasharray="${PHASE_STYLE[p].dash}" stroke-linejoin="round"/>`
      + `<text x="${Math.min(ex + 4, W0 - 4)}" y="${ey + 4}" class="pf-label">${p}</text>`;
  }).join('');
  const legend = [...'ABC'].map((p, k) => `<g transform="translate(${m.l + k * 80},12)"><line x1="0" x2="22" y1="0" y2="0" stroke="${PHASE_STYLE[p].color}" stroke-width="2" stroke-dasharray="${PHASE_STYLE[p].dash}"/><text x="27" y="4">Phase ${p}</text></g>`).join('');
  S.profilePoints = pts.map(([x, y, p, id]) => [X(x), Y(y), p, id]);
  return `<div class="rep-title">Voltage Profile - Source to ${esc(target)} <span class="pf-hint">(select a node or section to trace its path; points are all buses)</span></div>
    <div class="pf-wrap"><svg class="pf" width="${W0}" height="${H0}" viewBox="0 0 ${W0} ${H0}" role="img" aria-label="Voltage in percent versus distance from the source">
      <rect class="pf-plot" x="${m.l}" y="${m.t}" width="${W0 - m.l - m.r}" height="${H0 - m.t - m.b}"/>
      ${grid}${limitLines}${dots}${lines}${legend}
      <line x1="${m.l}" x2="${W0 - m.r}" y1="${H0 - m.b}" y2="${H0 - m.b}" class="pf-axis"/>
      <text x="${(W0 + m.l - m.r) / 2}" y="${H0 - 4}" text-anchor="middle" class="pf-muted">Distance from source (km)</text>
      <text transform="translate(13,${(H0 + m.t - m.b) / 2}) rotate(-90)" text-anchor="middle" class="pf-muted">Voltage (%)</text>
      <circle class="pf-hover" r="5" hidden/>
    </svg><div class="pf-tip" hidden></div></div>`;
}

function niceStep(raw) {
  const p = 10 ** Math.floor(Math.log10(raw || 1));
  const f = raw / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
}

function profileHover(root, e) {
  const svg = root.querySelector('svg.pf'); if (!svg || !S.profilePoints) return;
  const r = svg.getBoundingClientRect(), vb = svg.viewBox.baseVal;
  const k = vb.width / r.width;
  const x = (e.clientX - r.left) * k, y = (e.clientY - r.top) * k;
  let best = null, bd = 12 * k * 12 * k;
  for (const q of S.profilePoints) { const d = (q[0] - x) ** 2 + (q[1] - y) ** 2; if (d < bd) { bd = d; best = q; } }
  const tip = root.querySelector('.pf-tip'), mark = svg.querySelector('.pf-hover');
  if (!best) { tip.hidden = true; mark.setAttribute('hidden', ''); svg.style.cursor = ''; return; }
  const id = best[3], d = S.last.profile[id], v = S.last.nodes[id];
  mark.removeAttribute('hidden'); mark.setAttribute('cx', best[0]); mark.setAttribute('cy', best[1]);
  tip.hidden = false;
  tip.innerHTML = `<b>${esc(id)}</b><br>${fmt(d.distance_m / 1000, 3)} km${[...'ABC'].filter((p) => v[p]).map((p) => `<br><i style="border-color:${PHASE_STYLE[p].color}"></i>V${p.toLowerCase()} ${fmt(v[p].pct)} %`).join('')}`;
  tip.style.left = `${Math.min(e.clientX - r.left + 14, r.width - 150)}px`; tip.style.top = `${e.clientY - r.top + 10}px`;
  svg.style.cursor = 'pointer'; svg.dataset.hit = id;
}

/* Contenedor de informe (panel inferior o ventana desacoplada). */
function renderReports() {
  const kind = S.last ? S.last.analysis : null;
  if (!reportAvailable(S.report, kind)) S.report = 'messages';
  $('#reportTabs').innerHTML = REPORTS.filter(([k]) => reportAvailable(k, kind))
    .map(([k]) => `<button data-tab="${k}" class="${k === S.report ? 'active' : ''}">${reportLabel(k)}</button>`).join('');
  renderReportInto($('#reportBody'), S.report, false);
  for (const [key, w] of S.popouts) {
    if (w.closed) { S.popouts.delete(key); continue; }
    w.document.title = `${reportLabel(key)} - Ñandu`;
    w.document.querySelector('.dock-title span').textContent = reportLabel(key);
    renderReportInto(w.document.getElementById('reportBody'), key, true);
  }
}

function renderReportInto(root, key, detached) {
  wireReport(root);
  root.dataset.key = key;
  const L = S.last;
  if (!L) { root.innerHTML = '<p class="empty">Run an analysis to display results.</p>'; return; }
  const grid = GRIDS[key] && !(key === 'profile' && !L.profile);
  const spec = grid ? GRIDS[key](L, PREFIX[L.analysis] || 'Load Flow') : null;
  const st = spec ? gridState(key, spec) : null;
  root.innerHTML = `<div class="rep-tools">
    ${spec ? `<input class="rep-filter" placeholder="Filter..." value="${esc(st.filter)}">
      <details class="rep-cols"><summary>Columns</summary><div>${spec.cols.map((c, i) => `<label><input type="checkbox" data-col="${i}" ${st.hidden.has(i) ? '' : 'checked'}> ${esc(c.label)}</label>`).join('')}</div></details>` : ''}
    <span class="spacer"></span>
    <button data-export="copy" title="Copy to clipboard (tab separated)">Copy</button>
    <button data-export="csv" title="Export to CSV">CSV</button>
    <button data-export="xlsx" title="Export to Excel">Excel</button>
    ${detached ? '' : '<button data-export="popout" title="Open this report in its own window">Detach ⧉</button>'}
  </div>
  ${key === 'profile' ? `<div class="rep-chart">${profileChart(L, { w: root.clientWidth - 24, h: root.clientHeight - 120 })}</div>` : ''}
  <div class="rep-content"></div>`;
  refreshContent(root);
}

function refreshContent(root) {
  const key = root.dataset.key, L = S.last;
  const content = root.querySelector('.rep-content');
  const prefix = PREFIX[L.analysis] || 'Load Flow';
  if (GRIDS[key] && (key !== 'profile' || L.profile)) {
    const spec = GRIDS[key](L, prefix);
    content.innerHTML = `${key === 'profile' ? '' : `<div class="rep-title">${esc(spec.title)}</div>`}${gridTable(spec, gridState(key, spec))}`;
  } else content.innerHTML = (HTML_REPORTS[key] || HTML_REPORTS.messages)(L, prefix);
}

function wireReport(root) {
  if (root.dataset.wired) return;
  root.dataset.wired = '1';
  const spec = () => GRIDS[root.dataset.key](S.last, PREFIX[S.last.analysis] || 'Load Flow');
  root.addEventListener('input', (e) => {
    if (!e.target.classList.contains('rep-filter')) return;
    gridState(root.dataset.key, spec()).filter = e.target.value;
    refreshContent(root);
  });
  root.addEventListener('change', (e) => {
    const col = e.target.dataset.col; if (col === undefined) return;
    const st = gridState(root.dataset.key, spec());
    if (e.target.checked) st.hidden.delete(+col); else st.hidden.add(+col);
    refreshContent(root);
  });
  root.addEventListener('mousemove', (e) => { if (e.target.closest('svg.pf')) profileHover(root, e); });
  root.addEventListener('click', (e) => {
    const th = e.target.closest('th.sortable');
    if (th) {
      const st = gridState(root.dataset.key, spec()), i = +th.dataset.col;
      st.sort = !st.sort || st.sort[0] !== i ? [i, 1] : st.sort[1] > 0 ? [i, -1] : null;
      refreshContent(root); return;
    }
    const btn = e.target.closest('[data-export]');
    if (btn) { exportReport(root, btn.dataset.export); return; }
    const svg = e.target.closest('svg.pf');
    const hit = svg && svg.dataset.hit ? { kind: 'node', id: svg.dataset.hit } : null;
    const tr = e.target.closest('tr.clickable');
    const target = hit || (tr && tr.dataset.id ? { kind: tr.dataset.kind, id: tr.dataset.id } : null);
    if (target && ((target.kind === 'node' && S.nodes.has(target.id)) || S.sectionById.has(target.id))) {
      select(target); zoomTo(target);
      if (root.ownerDocument !== document) window.focus();
    }
  });
}

/* ---------------- Exportación ---------------- */
// Bloques {title, header, rows} con valores crudos: números como números, sin separadores de miles.
function reportBlocks(root) {
  const key = root.dataset.key, L = S.last;
  if (GRIDS[key] && (key !== 'profile' || L.profile)) {
    const spec = GRIDS[key](L, PREFIX[L.analysis] || 'Load Flow'), st = gridState(key, spec);
    const idx = spec.cols.map((_, i) => i).filter((i) => !st.hidden.has(i));
    return [{ title: spec.title, header: idx.map((i) => spec.cols[i].label),
      rows: gridRows(spec, st).map((r) => idx.map((i) => (typeof r.v[i] === 'number' && spec.cols[i].d !== undefined ? +r.v[i].toFixed(Math.max(spec.cols[i].d, 4)) : r.v[i]))) }];
  }
  const blocks = [];
  let title = reportLabel(key);
  for (const el of root.querySelectorAll('.rep-content .rep-title, .rep-content table.rep')) {
    if (el.matches('.rep-title')) { title = el.textContent.trim(); continue; }
    const trs = [...el.rows];
    blocks.push({ title, header: [...trs[0].cells].map((c) => c.textContent.trim()), rows: trs.slice(1).map((tr) => [...tr.cells].map((c) => parseCell(c.textContent.trim()))) });
    title = '';
  }
  return blocks;
}

function parseCell(t) {
  return /^-?[\d,]*\.?\d+(e[-+]?\d+)?$/i.test(t) && t !== '' ? Number(t.replace(/,/g, '')) : t;
}

function exportReport(root, how) {
  const key = root.dataset.key;
  if (how === 'popout') { detachReport(key); return; }
  if (!S.last) return;
  const blocks = reportBlocks(root);
  const base = `${(S.file || 'nandu').split(/[\\/]/).pop().replace(/\.nandu\.sqlite$|\.sqlite$/, '')}-${S.last.analysis}-${key}`;
  const lines = (sep, q) => blocks.flatMap((b) => [...(b.title ? [[b.title]] : []), b.header, ...b.rows, []]).map((r) => r.map((v) => q(v ?? '')).join(sep)).join('\r\n');
  if (how === 'copy') {
    navigator.clipboard.writeText(lines('\t', (v) => String(v).replace(/[\t\r\n]+/g, ' ')))
      .then(() => status(`${reportLabel(key)} copied to clipboard`), (e) => status('Error: ' + e.message));
  } else if (how === 'csv') {
    const q = (v) => (/[",\r\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
    download(new Blob(['﻿' + lines(',', q)], { type: 'text/csv;charset=utf-8' }), base + '.csv');
  } else if (how === 'xlsx') {
    download(xlsx(blocks, reportLabel(key)), base + '.xlsx');
  }
}

function download(blob, name) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  status(`Exported ${name}`);
}

// XLSX mínimo (Office Open XML) en un ZIP sin compresión; celdas numéricas como números.
const xmlEsc = (v) => String(v).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])).replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g, '');
const colName = (i) => { let s = ''; for (i += 1; i; i = Math.floor((i - 1) / 26)) s = String.fromCharCode(65 + ((i - 1) % 26)) + s; return s; };

function xlsx(blocks, sheetName) {
  const rows = [];
  const add = (cells, bold) => rows.push({ cells, bold });
  for (const b of blocks) { if (b.title) add([b.title], true); add(b.header, true); b.rows.forEach((r) => add(r, false)); add([], false); }
  const sheet = rows.map((r, ri) => `<row r="${ri + 1}">${r.cells.map((v, ci) => {
    const ref = `${colName(ci)}${ri + 1}`, s = r.bold ? ' s="1"' : '';
    if (v === null || v === undefined || v === '') return '';
    return typeof v === 'number' && Number.isFinite(v) ? `<c r="${ref}"${s}><v>${v}</v></c>` : `<c r="${ref}" t="inlineStr"${s}><is><t xml:space="preserve">${xmlEsc(v)}</t></is></c>`;
  }).join('')}</row>`).join('');
  const widths = [];
  rows.forEach((r) => { if (r.cells.length > 1) r.cells.forEach((v, i) => { widths[i] = Math.max(widths[i] || 8, Math.min(40, String(v ?? '').length + 2)); }); });
  const NS = 'http://schemas.openxmlformats.org';
  const files = {
    '[Content_Types].xml': `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="${NS}/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>`,
    '_rels/.rels': `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="${NS}/package/2006/relationships"><Relationship Id="rId1" Type="${NS}/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>`,
    'xl/workbook.xml': `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="${NS}/spreadsheetml/2006/main" xmlns:r="${NS}/officeDocument/2006/relationships"><sheets><sheet name="${xmlEsc(sheetName.replace(/[\[\]:*?/\\]/g, ' ').slice(0, 31))}" sheetId="1" r:id="rId1"/></sheets></workbook>`,
    'xl/_rels/workbook.xml.rels': `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="${NS}/package/2006/relationships"><Relationship Id="rId1" Type="${NS}/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="${NS}/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>`,
    'xl/styles.xml': `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="${NS}/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs></styleSheet>`,
    'xl/worksheets/sheet1.xml': `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="${NS}/spreadsheetml/2006/main">${widths.length ? `<cols>${widths.map((w, i) => `<col min="${i + 1}" max="${i + 1}" width="${w || 8}" customWidth="1"/>`).join('')}</cols>` : ''}<sheetData>${sheet}</sheetData></worksheet>`,
  };
  return new Blob([zipStore(files)], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
}

const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; }
  return t;
})();
function crc32(bytes) { let c = 0xFFFFFFFF; for (const b of bytes) c = CRC_TABLE[(c ^ b) & 255] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; }

function zipStore(files) {
  const enc = new TextEncoder(), parts = [], central = [];
  let offset = 0;
  for (const [name, text] of Object.entries(files)) {
    const nb = enc.encode(name), data = enc.encode(text), crc = crc32(data);
    const head = (size, sig) => { const v = new DataView(new ArrayBuffer(size)); v.setUint32(0, sig, true); return v; };
    const loc = head(30, 0x04034b50);
    loc.setUint16(4, 20, true); loc.setUint16(6, 0x0800, true); loc.setUint16(12, 0x21, true);
    loc.setUint32(14, crc, true); loc.setUint32(18, data.length, true); loc.setUint32(22, data.length, true); loc.setUint16(26, nb.length, true);
    const cen = head(46, 0x02014b50);
    cen.setUint16(4, 20, true); cen.setUint16(6, 20, true); cen.setUint16(8, 0x0800, true); cen.setUint16(14, 0x21, true);
    cen.setUint32(16, crc, true); cen.setUint32(20, data.length, true); cen.setUint32(24, data.length, true); cen.setUint16(28, nb.length, true);
    cen.setUint32(42, offset, true);
    parts.push(new Uint8Array(loc.buffer), nb, data);
    central.push(new Uint8Array(cen.buffer), nb);
    offset += 30 + nb.length + data.length;
  }
  const size = central.reduce((s, p) => s + p.length, 0);
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true); end.setUint16(8, Object.keys(files).length, true); end.setUint16(10, Object.keys(files).length, true);
  end.setUint32(12, size, true); end.setUint32(16, offset, true);
  return new Blob([...parts, ...central, new Uint8Array(end.buffer)]);
}

/* ---------------- Ventanas desacopladas ---------------- */
function detachReport(key) {
  let w = S.popouts.get(key);
  if (!w || w.closed) {
    w = window.open('', `nandu-report-${key}`, 'width=1000,height=620');
    if (!w) { status('The browser blocked the report window (allow pop-ups for 127.0.0.1)'); return; }
    w.document.open();
    w.document.write(`<!doctype html><html lang="en"><head><meta charset="utf-8"><title>${esc(reportLabel(key))} - Ñandu</title>
      <link rel="stylesheet" href="${new URL('style.css', location.href).href}"></head>
      <body class="popout"><div class="dock-title"><span>${esc(reportLabel(key))}</span></div><div id="reportBody" class="dock-body"></div></body></html>`);
    w.document.close();
    S.popouts.set(key, w);
  }
  renderReports();
  if (!w.nandu_wired) {
    w.nandu_wired = true;
    w.addEventListener('resize', () => { clearTimeout(w.nandu_timer); w.nandu_timer = setTimeout(() => { if (key === 'profile') renderReports(); }, 150); });
  }
  w.focus();
}
window.addEventListener('beforeunload', () => { for (const w of S.popouts.values()) if (!w.closed) w.close(); });

$('#reportTabs').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) { S.report = b.dataset.tab; renderReports(); } });

/* ---------------- Análisis ---------------- */
const METHOD_OPTIONS = `<option value="KVAMethod">Connected kVA</option><option value="ActualKVAMethod">Actual kVA</option><option value="KWHMethod">Consumption kWh</option>`;

function openAnalysis(kind) {
  const lf = kind === 'loadflow';
  $('#dlgTitle').textContent = { loadflow: 'Load Flow Analysis', allocate: 'Load Allocation Analysis', estimate: 'Distribution State Estimator Analysis' }[kind];
  const nets = S.geom ? S.geom.networks.map((n) => n.id).join(', ') : '';
  if (kind === 'allocate') {
    $('#dlgBody').innerHTML = `
    <fieldset><legend>Allocation Method</legend><div class="kv"><span>Method</span><select id="optMethod">${METHOD_OPTIONS}</select></div></fieldset>
    <fieldset><legend>Load Adjustments</legend><div class="kv">
      <span>Adjust loads using load flow</span><span>Yes</span><span>Tolerance (%)</span><span>0.5</span></div></fieldset>
    <fieldset><legend>Networks and Meters</legend><div class="kv"><span>Networks</span><span>${esc(nets)}</span>
      <span>Demand</span><span>Network meters at the source (A-PF per phase)</span></div></fieldset>
    <p class="note">Locked loads keep their values. Loads without data for the method receive 0, as in CYME.</p>`;
  } else $('#dlgBody').innerHTML = lf ? `
    <fieldset><legend>Calculation method</legend><div class="kv">
      <span>Method</span><span>Newton-Raphson unbalanced (augmented nodal)</span>
      <span>Tolerance</span><span>1e-9 pu (CYME DEFAULT: VoltageDrop 0.1 %)</span>
      <span>Maximum iterations</span><span>40</span></div></fieldset>
    <fieldset><legend>Options</legend><div class="kv">
      <span>Include source impedance</span><span>No (as CYME DEFAULT)</span>
      <span>Include line charging</span><span>Yes</span>
      <span>Regulator tap operation</span><span>Locked at specified positions</span></div></fieldset>
    <fieldset><legend>Networks</legend><div class="kv"><span>Analysed networks</span><span>${esc(nets)}</span></div></fieldset>`
    : `
    <fieldset><legend>Convergence parameters</legend><div class="kv">
      <span>Algorithm</span><span>Weighted least squares, Lagrange multipliers (Hachtel)</span>
      <span>Tolerance</span><span>1e-7 pu</span><span>Maximum iterations</span><span>60</span></div></fieldset>
    <fieldset><legend>Load initialization</legend><div class="kv">
      <span>Initialization mode</span><select id="optInit"><option value="None">None</option><option value="LoadAllocation">Load Allocation</option></select>
      <span>Load allocation method</span><select id="optMethod">${METHOD_OPTIONS}</select></div></fieldset>
    <fieldset><legend>Solution Quality Indices</legend><div class="kv">
      <span>Solution cost (χ²) and normalized residuals</span><label><input type="checkbox" id="optQuality"> Calculate (slower)</label></div></fieldset>
    <fieldset><legend>Load accuracy</legend><div class="kv">
      <span>Active power error (%)</span><span>50</span><span>Reactive power error (%)</span><span>50</span></div></fieldset>
    <fieldset><legend>Meter accuracy (global)</legend><div class="kv">
      <span>Current error (%)</span><span>3</span><span>Power factor error (%)</span><span>3</span></div></fieldset>
    <fieldset><legend>Meters</legend><div class="kv"><span>Source</span><span>Network meters (CYMNETWORKMETER)</span></div></fieldset>`;
  for (const [id, key] of [['optMethod', 'method'], ['optInit', 'initialization']]) {
    const el = $('#' + id);
    if (el && S.options[key]) el.value = S.options[key];
  }
  if ($('#optQuality')) $('#optQuality').checked = !!S.options.quality;
  const dlg = $('#analysisDialog');
  dlg.returnValue = '';
  dlg.onclose = () => {
    if (dlg.returnValue !== 'run') return;
    const options = {};
    if ($('#optMethod')) options.method = S.options.method = $('#optMethod').value;
    if ($('#optInit')) options.initialization = S.options.initialization = $('#optInit').value;
    if ($('#optQuality')) options.quality = S.options.quality = $('#optQuality').checked;
    runAnalysis(kind, options);
  };
  dlg.showModal();
}

async function runAnalysis(kind, options = {}) {
  if (!S.file) return;
  busy(true, `${PREFIX[kind]} running...`);
  const t0 = performance.now();
  try {
    S.last = await api('run', { file: S.file, analysis: kind, options });
    if ($('#colorLayer').value === 'network') $('#colorLayer').value = 'voltage';
    S.report = S.last.status === 'converged' ? 'summary' : 'messages';
    $('#center').classList.remove('no-results');
    renderReports(); draw();
    if (S.selection) select(S.selection);
    status(`${PREFIX[kind]}: ${S.last.status} in ${S.last.iterations.length} iterations (${((performance.now() - t0) / 1000).toFixed(2)} s)`);
  } catch (e) { status('Error: ' + e.message); }
  busy(false); resize();
}

/* ---------------- Edición de red ---------------- */
const DEVICE_CODES = { Switch: 13, Fuse: 14, Breaker: 8, Recloser: 10, Regulator: 4, ShuntCapacitor: 17, SpotLoad: 20 };
const DEVICE_LABEL = { 13: 'Switch', 14: 'Fuse', 8: 'Breaker', 10: 'Recloser', 4: 'Regulator', 17: 'Shunt Capacitor', 20: 'Spot Load' };
const LINE_CODES = new Set(['OverheadLine', 'OverheadLineUnbalanced', 'Underground']);

function toggleEdit(on = !S.edit) {
  S.edit = on;
  if (!on) cancelPending();
  document.body.classList.toggle('edit-mode', on);
  $$('[data-action="toggle-edit"]').forEach((b) => b.classList.toggle('active', on));
  renderProperties(S.props);
  status(on ? 'Edit mode: drag nodes to move them, right-click a section or node for more actions' : 'Edit mode off');
}

function setPoint(n, [wx, wy]) { if (mapEnabled()) { n.mx = wx; n.my = wy; } else { n.x = wx; n.y = wy; } }

async function edit(op, path = 'edit') {
  if (!S.file) return null;
  busy(true, 'Applying changes...');
  try {
    const data = await api(path, { file: S.file, edit: op });
    S.catalog = null;  // las plantillas disponibles pueden cambiar con el alta o la baja de equipos
    applyNetwork(data);
    renderReports(); draw();
    // Lo seleccionado puede haber desaparecido (tramo borrado o deshecho).
    const sel = S.selection;
    const alive = sel && (sel.kind === 'node' ? S.nodes.has(sel.id) : S.sectionById.has(sel.id));
    await select(alive ? sel : null);
    status(`${describeEdit(data.entry, path)} - results cleared, run the analysis again`);
    return data.entry;
  } catch (e) { status('Error: ' + e.message); alert(e.message); return null; }
  finally { busy(false); }
}
const undo = () => (S.edits && S.edits.count ? edit(null, 'undo') : status('Nothing to undo'));

function describeEdit(entry, path) {
  if (!entry) return '';
  const what = {
    set: () => `${entry.table} ${Object.values(entry.key).join(' / ')}: ${Object.keys(entry.after).join(', ')}`,
    batch: () => `${entry.ops.length} changes`,
    move_node: () => `Node ${entry.node} moved`,
    add_section: () => `Section ${entry.section} added (${fmt(entry.length_m, 1)} m)`,
    delete_section: () => `Section ${entry.section} deleted`,
    add_device: () => `${DEVICE_LABEL[entry.type]} ${entry.device} added (copied from ${entry.template})`,
    delete_device: () => `${DEVICE_LABEL[entry.type]} ${entry.device} deleted`,
  }[entry.op];
  return (path === 'undo' ? 'Undone: ' : '') + (what ? what() : entry.op);
}

function finishNodeDrag(d) {
  const [x, y] = point(d.node);
  edit({ op: 'move_node', node: d.node.id, x, y, map: mapEnabled() }).then((entry) => {
    if (!entry && S.nodes.get(d.node.id) === d.node) { setPoint(d.node, d.start); draw(); }
  });
}

/* Menú contextual: acciones del tramo o del nodo bajo el cursor. */
async function showContextMenu(hit, x, y) {
  const items = [];
  if (hit.kind === 'node') {
    items.push(['Add section from this node', () => startAddSection(hit.id)]);
  } else {
    const sec = S.sectionById.get(hit.id);
    const cat = await catalog();
    const add = Object.entries(cat.devices).filter(([code, d]) => d.available || [13, 14, 8, 10].includes(Number(code)))
      .map(([code]) => [`${DEVICE_LABEL[code]}...`, () => addDevice(sec.id, Number(code))]);
    items.push(['Add device', add]);
    for (const d of sec.devices) {
      if (LINE_CODES.has(d.type) || !DEVICE_CODES[d.type]) continue;
      items.push([`Delete ${d.type} ${d.number}`, () => confirm(`Delete ${d.type} ${d.number}?`) && edit({ op: 'delete_device', section: sec.id, type: DEVICE_CODES[d.type], device: d.number })]);
    }
    items.push(['Add section from To node', () => startAddSection(sec.to)]);
    items.push(['Delete section', () => deleteSection(sec.id)]);
  }
  const menu = $('#ctxMenu');
  const html = (list) => list.map(([label, fn], i) => (Array.isArray(fn)
    ? `<div class="ctx-sub">${esc(label)} ▸<div class="ctx-list">${html(fn)}</div></div>`
    : `<button data-i="${menu.actions.push(fn) - 1}">${esc(label)}</button>`)).join('');
  menu.actions = [];
  menu.innerHTML = html(items);
  menu.hidden = false;
  const r = menu.getBoundingClientRect();
  menu.style.left = Math.min(x, innerWidth - r.width - 4) + 'px';
  menu.style.top = Math.min(y, innerHeight - r.height - 4) + 'px';
}
function hideContextMenu() { $('#ctxMenu').hidden = true; }
$('#ctxMenu').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-i]'); if (!b) return;
  hideContextMenu(); $('#ctxMenu').actions[Number(b.dataset.i)]();
});
document.addEventListener('mousedown', (e) => { if (!e.target.closest('#ctxMenu')) hideContextMenu(); });

function deleteSection(id) {
  if (confirm(`Delete section ${id} with all its devices?`)) edit({ op: 'delete_section', section: id });
}

async function catalog() {
  if (!S.catalog) S.catalog = await api('catalog?file=' + encodeURIComponent(S.file));
  return S.catalog;
}

/* Diálogo de formulario genérico; devuelve los valores o null si se cancela. */
function formDialog(title, body, onOpen) {
  const dlg = $('#editDialog');
  $('#editTitle').textContent = title;
  $('#editBody').innerHTML = body;
  if (onOpen) onOpen($('#editBody'));
  dlg.returnValue = '';
  return new Promise((resolve) => {
    dlg.onclose = () => resolve(dlg.returnValue === 'ok' ? Object.fromEntries(new FormData($('#editForm'))) : null);
    dlg.showModal();
  });
}
const optionList = (items, selected) => items.map(([v, t]) => `<option value="${esc(v)}" ${String(v) === String(selected) ? 'selected' : ''}>${esc(t)}</option>`).join('');

async function addDevice(section, code) {
  const cat = await catalog();
  const dev = cat.devices[code];
  const loc = [1, 4, 8, 10, 13, 14].includes(code) ? 1 : 2;
  const values = await formDialog(`Add ${DEVICE_LABEL[code]} on section ${section}`, `<div class="kv">
    <span>Device Number</span><input name="number" placeholder="automatic">
    <span>Location</span><select name="location">${optionList(cat.locations, loc)}</select>
    ${dev.equipment.length ? `<span>Equipment</span><select name="equipment"><option value="">(same as template)</option>${optionList(dev.equipment.map((e) => [e, e]))}</select>` : ''}
    </div><p class="note">The new device copies the settings of an existing ${DEVICE_LABEL[code]} of the network; edit them afterwards in Properties.${code === 20 ? ' The load starts at 0 kW / 0 kvar.' : ''}</p>`);
  if (!values) return;
  edit({ op: 'add_device', section, type: code, number: values.number.trim() || undefined, location: Number(values.location), equipment: values.equipment || undefined });
}

/* Alta de tramo: desde un nodo hasta otro nodo o un punto nuevo, como el dibujo de tramos de CYME. */
function startAddSection(from) {
  if (!S.edit) toggleEdit(true);
  S.pending = { from, mouse: null };
  canvas.classList.add('drawing');
  status(`Add section from ${from}: click an existing node or an empty point (Esc or right-click cancels)`);
}
function cancelPending() {
  if (!S.pending) return;
  S.pending = null; canvas.classList.remove('drawing'); draw(); status('Cancelled');
}
function drawPending() {
  if (!S.pending || !S.pending.mouse || !S.nodes.has(S.pending.from)) return;
  const a = screenNode(S.nodes.get(S.pending.from));
  ctx.save(); ctx.strokeStyle = '#1e88e5'; ctx.lineWidth = 1.5; ctx.setLineDash([6, 4]);
  ctx.beginPath(); ctx.moveTo(...a); ctx.lineTo(...S.pending.mouse); ctx.stroke(); ctx.restore();
}

async function pickSectionEnd(sx, sy) {
  const { from } = S.pending;
  const hit = hitTest(sx, sy);
  const to = hit && hit.kind === 'node' && hit.id !== from ? hit.id : null;
  const [wx, wy] = toWorld(sx, sy);
  S.pending = null; canvas.classList.remove('drawing');
  const cat = await catalog();
  const start = S.nodes.get(from);
  const end = to ? point(S.nodes.get(to)) : [wx, wy];
  // Longitud sugerida: distancia en el plano del proyecto (en el mapa, el servidor la calcula en UTM).
  const drawn = mapEnabled() ? null : Math.hypot(end[0] - start.x, end[1] - start.y);
  const upstream = S.sections.find((s) => s.to === from || s.from === from);
  const phase = upstream ? (cat.phases.find(([, t]) => t === upstream.phases) || [7])[0] : 7;
  const values = await formDialog(`Add section from ${from}`, `<div class="kv">
    <span>To Node</span><span>${to ? esc(to) : 'new node at the clicked point'}</span>
    <span>Section Id</span><input name="section" placeholder="automatic">
    ${to ? '' : '<span>New Node Id</span><input name="node" placeholder="automatic">'}
    <span>Phase</span><select name="phase">${optionList(cat.phases, phase)}</select>
    <span>Conductor</span><select name="line_type">${optionList(Object.entries(cat.lines).map(([c, l]) => [c, l.name]), 2)}</select>
    <span>Equipment</span><select name="equipment"></select>
    <span>Length (m)</span><input name="length" placeholder="${drawn === null ? 'from coordinates' : fmt(drawn, 1)}">
    </div><p class="note">The conductor copies an existing one of the same type; the length defaults to the drawn distance.</p>`, (body) => {
    const fill = () => { $('[name=equipment]', body).innerHTML = optionList(cat.lines[$('[name=line_type]', body).value].equipment.map((e) => [e, e])); };
    $('[name=line_type]', body).addEventListener('change', fill); fill();
  });
  draw();
  if (!values) { status('Cancelled'); return; }
  const op = { op: 'add_section', from, phase: Number(values.phase), line_type: Number(values.line_type),
    equipment: values.equipment || undefined, section: values.section.trim() || undefined,
    length: values.length.trim() ? Number(values.length.replace(',', '.')) : undefined };
  if (to) op.to = to; else Object.assign(op, { x: wx, y: wy, map: mapEnabled(), node: (values.node || '').trim() || undefined });
  const entry = await edit(op);
  if (entry) select({ kind: 'section', id: entry.section });
}

async function saveAs() {
  if (!S.edits || !S.edits.count) { status('No changes to save'); return; }
  const base = S.file.split(/[\\/]/).pop().replace(/\.nandu\.sqlite$/, '');
  const values = await formDialog('Save As', `<div class="kv"><span>File name</span><input name="name" value="${esc(base)}-edited" size="40"><span></span><span>.nandu.sqlite in the data folder</span></div>
    <p class="note">The edited network is saved as a new project with the list of changes. The original file is not modified.</p>`);
  if (!values) return;
  busy(true, 'Saving...');
  try {
    const { file } = await api('save-as', { file: S.file, name: values.name.trim() });
    busy(false);
    await openFile(file);
    status(`Saved as ${file.split(/[\\/]/).pop()}`);
  } catch (e) { busy(false); status('Error: ' + e.message); alert(e.message); }
}

/* ---------------- Menús y acciones ---------------- */
function action(name) {
  switch (name) {
    case 'open': openFileDialog(); break;
    case 'reload': if (S.file) openFile(S.file); break;
    case 'zoom-extents': fitExtents(); break;
    case 'zoom-in': zoomAt(1.5); break;
    case 'zoom-out': zoomAt(1 / 1.5); break;
    case 'toggle-explorer': $('#workspace').classList.toggle('no-explorer'); setTimeout(resize); break;
    case 'toggle-properties': $('#workspace').classList.toggle('no-properties'); setTimeout(resize); break;
    case 'toggle-results': $('#center').classList.toggle('no-results'); setTimeout(resize); break;
    case 'dialog-loadflow': openAnalysis('loadflow'); break;
    case 'dialog-estimate': openAnalysis('estimate'); break;
    case 'dialog-allocate': openAnalysis('allocate'); break;
    case 'save-as': saveAs(); break;
    case 'toggle-edit': toggleEdit(); break;
    case 'undo': undo(); break;
    case 'delete-selection':
      if (S.selection && S.selection.kind === 'section') deleteSection(S.selection.id); else status('Select a section to delete');
      break;
    case 'add-section':
      if (S.selection) startAddSection(S.selection.kind === 'node' ? S.selection.id : S.sectionById.get(S.selection.id).to);
      else status('Select the start node or section first');
      break;
    case 'about': alert('Ñandu - distribution network analysis.\nNodal engine reproducing CYME 9 load flow and state estimator.'); break;
    default: break;
  }
}

async function openFileDialog() {
  const { files } = await api('files');
  $('#fileList').innerHTML = files.map((f) => `<option value="${esc(f)}" ${f === S.file ? 'selected' : ''}>${esc(f.split(/[\\/]/).pop())}</option>`).join('');
  const dlg = $('#openDialog'); dlg.returnValue = '';
  dlg.onclose = () => { if (dlg.returnValue === 'open' && $('#fileList').value) openFile($('#fileList').value); };
  dlg.showModal();
}

document.addEventListener('click', (e) => {
  const menu = e.target.closest('.menu');
  $$('.menu.open').forEach((m) => { if (m !== menu) m.classList.remove('open'); });
  if (menu && !e.target.closest('.dropdown')) menu.classList.toggle('open');
  const el = e.target.closest('[data-action],[data-report]');
  if (!el) return;
  if (e.target.closest('.dropdown')) menu.classList.remove('open');
  if (el.dataset.action) action(el.dataset.action);
  if (el.dataset.report) { S.report = el.dataset.report; $('#center').classList.remove('no-results'); renderReports(); setTimeout(resize); }
});
function updateMapAttribution() {
  $('#mapAttribution').hidden = !mapEnabled();
  for (const el of $$('#mapAttribution [data-provider]')) el.hidden = el.dataset.provider !== $('#mapLayer').value;
}
let previousMapLayer = 'none';
function changeMapLayer() {
  const changedProjection = (previousMapLayer === 'none') !== ($('#mapLayer').value === 'none');
  previousMapLayer = $('#mapLayer').value;
  updateMapAttribution();
  updateCrs();
  if (changedProjection) fitExtents();
  else draw();
}
['colorLayer', 'tagLayer', 'showDevices'].forEach((id) => $('#' + id).addEventListener('change', draw));
$('#mapLayer').addEventListener('change', changeMapLayer);
document.addEventListener('keydown', (e) => {
  const typing = e.target.closest('input, select, textarea');
  if (e.key === 'Escape') { hideContextMenu(); if (S.pending) cancelPending(); else select(null); }
  if (e.key === 'F5') { e.preventDefault(); openAnalysis('loadflow'); }
  if (typing) return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); undo(); }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); saveAs(); }
  if (e.key === 'Delete' && S.edit) action('delete-selection');
});
new ResizeObserver(() => resize()).observe($('#viewWrap'));

/* Altura del panel Simulation Result: se arrastra desde su barra de título (como un dock de CYME). */
const RESULTS_KEY = 'nandu.resultsHeight';
const RESULTS_MIN = 90;
function resultsMax() { return $('#center').clientHeight - 22 - 6 - 80; }  // deja al menos 80 px de unifilar
function setResultsHeight(px, save = true) {
  const h = Math.round(Math.max(RESULTS_MIN, Math.min(resultsMax(), px)));
  $('#center').style.setProperty('--results-h', `${h}px`);
  if (save) try { localStorage.setItem(RESULTS_KEY, String(h)); } catch (e) { /* sin almacenamiento: solo esta sesión */ }
}
try { const saved = Number(localStorage.getItem(RESULTS_KEY)); if (saved) setResultsHeight(saved, false); } catch (e) { /* idem */ }
let resultsDrag = null;
$('#results > .dock-title').addEventListener('mousedown', (e) => {
  if (e.button !== 0 || e.target.closest('.dock-close')) return;
  resultsDrag = { y: e.clientY, h: $('#results').getBoundingClientRect().height };
  document.body.classList.add('resizing-results');
  e.preventDefault();
});
document.addEventListener('mousemove', (e) => { if (resultsDrag) setResultsHeight(resultsDrag.h + resultsDrag.y - e.clientY); });
document.addEventListener('mouseup', () => {
  if (!resultsDrag) return;
  resultsDrag = null; document.body.classList.remove('resizing-results');
  refreshProfileSize();
});
// El perfil se dibuja al tamaño del panel: se redibuja al soltar el divisor o al cambiar la ventana.
function refreshProfileSize() { if (S.report === 'profile' && S.last && S.last.profile) renderReports(); }
let profileTimer = null;
window.addEventListener('resize', () => { clearTimeout(profileTimer); profileTimer = setTimeout(refreshProfileSize, 150); });
$('#results > .dock-title').addEventListener('dblclick', (e) => {
  if (e.target.closest('.dock-close')) return;
  const h = $('#results').getBoundingClientRect().height;
  if (h < resultsMax() - 4) { S.resultsRestore = h; setResultsHeight(resultsMax(), false); }
  else setResultsHeight(S.resultsRestore || 240);
  refreshProfileSize();
});
window.addEventListener('resize', () => { const h = $('#results').getBoundingClientRect().height; if (h > resultsMax()) setResultsHeight(resultsMax(), false); });
init();
