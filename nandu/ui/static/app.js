'use strict';
/* Ñandu UI: unifilar en canvas, explorador, propiedades e informes con el estilo de CYME. */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const fmt = (v, d = 2) => (v === null || v === undefined || v === '') ? '' : Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });

const S = {
  file: null, geom: null, explorer: [], nodes: new Map(), sections: [], sectionById: new Map(),
  last: null, view: { s: 1, tx: 0, ty: 0 }, selection: null, hover: null, report: 'messages', options: {},
  dpr: window.devicePixelRatio || 1,
};
const canvas = $('#view');
const ctx = canvas.getContext('2d');

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
    S.file = file; S.geom = data.geometry; S.explorer = data.explorer; S.last = data.last;
    S.nodes = new Map(S.geom.nodes.map((n) => [n.id, n]));
    S.sections = S.geom.sections.filter((s) => S.nodes.has(s.from) && S.nodes.has(s.to));
    S.sectionById = new Map(S.sections.map((s) => [s.id, s]));
    S.sourcesByNode = new Map(S.geom.sources.map((s) => [s.node, s]));
    S.netColor = new Map(S.geom.networks.map((n) => [n.id, n.color]));
    S.selection = null;
    const name = file.split(/[\\/]/).pop();
    $('#title').textContent = `Ñandu - [${name}]`;
    $('#viewTab').textContent = `${S.geom.networks.map((n) => n.id).join(', ')} - Geographic`;
    $('#crs').textContent = S.geom.crs || '';
    renderExplorer(); renderProperties(null); renderReports(); resize(); fitExtents();
    status(`Ready - ${S.sections.length} sections, ${S.nodes.size} nodes`);
  } catch (e) { status('Error: ' + e.message); }
  busy(false);
}

/* ---------------- Vista ---------------- */
function resize() {
  const r = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, r.width * S.dpr); canvas.height = Math.max(1, r.height * S.dpr);
  draw();
}
const W = () => canvas.width / S.dpr;
const H = () => canvas.height / S.dpr;
const toScreen = (x, y) => [x * S.view.s + S.view.tx, -y * S.view.s + S.view.ty];
const toWorld = (sx, sy) => [(sx - S.view.tx) / S.view.s, -(sy - S.view.ty) / S.view.s];

function fitExtents(bounds) {
  if (!S.geom) return;
  const [x0, y0, x1, y1] = bounds || S.geom.bounds;
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

function draw() {
  ctx.setTransform(S.dpr, 0, 0, S.dpr, 0, 0);
  ctx.clearRect(0, 0, W(), H());
  if (!S.geom) return;
  const sel = S.selection;
  // Selección debajo, como el halo de CYME.
  if (sel && sel.kind === 'section' && S.sectionById.has(sel.id)) {
    const sec = S.sectionById.get(sel.id);
    const a = toScreen(S.nodes.get(sec.from).x, S.nodes.get(sec.from).y);
    const b = toScreen(S.nodes.get(sec.to).x, S.nodes.get(sec.to).y);
    ctx.strokeStyle = 'rgba(51,153,255,.45)'; ctx.lineWidth = 9; ctx.setLineDash([]); ctx.lineCap = 'round';
    ctx.beginPath(); ctx.moveTo(...a); ctx.lineTo(...b); ctx.stroke();
  }
  ctx.lineCap = 'butt';
  for (const sec of S.sections) {
    const st = styleFor(sec);
    const a = toScreen(S.nodes.get(sec.from).x, S.nodes.get(sec.from).y);
    const b = toScreen(S.nodes.get(sec.to).x, S.nodes.get(sec.to).y);
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
    const n = S.nodes.get(sel.id); const [x, y] = toScreen(n.x, n.y);
    ctx.fillStyle = '#1e88e5';
    for (const [dx, dy] of [[-7, -7], [7, -7], [-7, 7], [7, 7], [0, -7], [0, 7], [-7, 0], [7, 0]]) ctx.fillRect(x + dx - 1.5, y + dy - 1.5, 3, 3);
  }
  drawTags();
  renderLegend();
}

function drawNodes() {
  ctx.fillStyle = '#1f2f6b';
  for (const n of S.nodes.values()) {
    const [x, y] = toScreen(n.x, n.y);
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
    const a = toScreen(S.nodes.get(sec.from).x, S.nodes.get(sec.from).y);
    const b = toScreen(S.nodes.get(sec.to).x, S.nodes.get(sec.to).y);
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
      ctx.rotate(ang + Math.PI / 2); ctx.fillStyle = c; ctx.beginPath(); ctx.moveTo(0, 2); ctx.lineTo(0, 7); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(-3.5, 7); ctx.lineTo(3.5, 7); ctx.lineTo(0, 12); ctx.closePath(); ctx.fill();
      break;
    default: break;
  }
  ctx.restore();
}

function drawSources() {
  for (const src of S.geom.sources) {
    const n = S.nodes.get(src.node); if (!n) continue;
    const [x, y] = toScreen(n.x, n.y);
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
      const [x, y] = toScreen(n.x, n.y);
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
      const a = toScreen(S.nodes.get(sec.from).x, S.nodes.get(sec.from).y);
      const b = toScreen(S.nodes.get(sec.to).x, S.nodes.get(sec.to).y);
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
    const [x, y] = toScreen(n.x, n.y);
    const d = Math.hypot(x - sx, y - sy);
    if (d < 6 && (!best || d < best.d)) best = { kind: 'node', id: n.id, d };
  }
  if (best) return best;
  for (const sec of S.sections) {
    const a = toScreen(S.nodes.get(sec.from).x, S.nodes.get(sec.from).y);
    const b = toScreen(S.nodes.get(sec.to).x, S.nodes.get(sec.to).y);
    const vx = b[0] - a[0], vy = b[1] - a[1];
    const t = Math.max(0, Math.min(1, ((sx - a[0]) * vx + (sy - a[1]) * vy) / (vx * vx + vy * vy || 1)));
    const d = Math.hypot(a[0] + t * vx - sx, a[1] + t * vy - sy);
    if (d < 5 && (!best || d < best.d)) best = { kind: 'section', id: sec.id, d };
  }
  return best;
}

let drag = null;
canvas.addEventListener('mousedown', (e) => { drag = { x: e.offsetX, y: e.offsetY, tx: S.view.tx, ty: S.view.ty, moved: false }; });
window.addEventListener('mouseup', (e) => {
  if (drag && !drag.moved && e.target === canvas) select(hitTest(e.offsetX, e.offsetY));
  drag = null; canvas.classList.remove('panning');
});
canvas.addEventListener('mousemove', (e) => {
  const [wx, wy] = toWorld(e.offsetX, e.offsetY);
  $('#coords').textContent = `X: ${wx.toFixed(2)}  Y: ${wy.toFixed(2)}`;
  if (drag) {
    const dx = e.offsetX - drag.x, dy = e.offsetY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) { drag.moved = true; canvas.classList.add('panning'); }
    if (drag.moved) { S.view.tx = drag.tx + dx; S.view.ty = drag.ty + dy; draw(); }
    return;
  }
  tooltip(hitTest(e.offsetX, e.offsetY), e.offsetX, e.offsetY);
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
  if (hit.kind === 'node') { const n = S.nodes.get(hit.id); [x0, y0, x1, y1] = [n.x - 150, n.y - 150, n.x + 150, n.y + 150]; }
  else {
    const sec = S.sectionById.get(hit.id); const a = S.nodes.get(sec.from), b = S.nodes.get(sec.to);
    [x0, y0, x1, y1] = [Math.min(a.x, b.x) - 150, Math.min(a.y, b.y) - 150, Math.max(a.x, b.x) + 150, Math.max(a.y, b.y) + 150];
  }
  fitExtents([x0, y0, x1, y1]);
}

async function select(hit) {
  S.selection = hit ? { kind: hit.kind, id: hit.id } : null;
  draw();
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
  $('#propSelect').textContent = data ? data.title : 'No selection';
  if (!data) { $('#propBody').innerHTML = ''; return; }
  $('#propBody').innerHTML = data.groups.map((g) => `<div class="pgroup ${g.collapsed ? 'collapsed' : ''} ${g.name === 'Load Flow' ? 'results' : ''}">
    <div class="phead">${esc(g.name)}</div><table>${g.fields.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v === null ? '' : v)}</td></tr>`).join('')}</table></div>`).join('');
}
$('#propBody').addEventListener('click', (e) => { const h = e.target.closest('.phead'); if (h) h.parentElement.classList.toggle('collapsed'); });

/* ---------------- Informes ---------------- */
const REPORTS = [
  ['messages', 'Messages'], ['summary', 'Summary Report'], ['abnormal', 'Abnormal conditions'], ['buses', 'Buses'],
  ['lines', 'Lines and Cables'], ['iterations', 'Iteration Report'], ['meters', 'DSE - Meter'], ['loads', 'DSE - Load Values'],
];

const PREFIX = { loadflow: 'Load Flow', allocate: 'Load Allocation', estimate: 'Distribution State Estimator' };

function renderReports() {
  const kind = S.last ? S.last.analysis : null;
  $('#reportTabs').innerHTML = REPORTS.filter(([k]) => (k !== 'meters' || kind === 'estimate') && (k !== 'loads' || ['estimate', 'allocate'].includes(kind)))
    .map(([k, label]) => `<button data-tab="${k}" class="${k === S.report ? 'active' : ''}">${k === 'loads' ? (kind === 'allocate' ? 'Load Allocation - Detailed' : label) : label}</button>`).join('');
  const body = $('#reportBody');
  const L = S.last;
  if (!L) { body.innerHTML = '<p class="empty">Run an analysis to display results.</p>'; return; }
  const prefix = PREFIX[L.analysis] || 'Load Flow';
  const R = {
    messages: () => L.messages.map((m) => `<div class="msg ${m.level}">${esc(m.text)}</div>`).join(''),
    summary: () => !L.summary ? '<p class="empty">No converged results.</p>' : Object.entries(L.summary).map(([net, rows]) =>
      `<div class="rep-title">${prefix} - Summary Report: ${esc(net)}</div><table class="rep"><tr><th>Total Summary</th><th class="num">kW</th><th class="num">kvar</th><th class="num">kVA</th><th class="num">PF(%)</th></tr>
      ${rows.map((r) => `<tr class="${r.item.startsWith('Total') ? 'total' : ''}"><td>${esc(r.item)}</td><td class="num">${fmt(r.kw)}</td><td class="num">${fmt(r.kvar)}</td><td class="num">${fmt(r.kva)}</td><td class="num">${fmt(r.pf)}</td></tr>`).join('')}</table>`).join(''),
    abnormal: () => !L.abnormal ? '' : `<div class="rep-title">${prefix} - Abnormal Conditions</div>
      <table class="rep"><tr><th>Condition</th><th>Phase</th><th class="num">Count</th><th>Worst Condition</th><th class="num">Value</th></tr>
      ${L.abnormal.rows.map((r) => `<tr class="clickable" data-kind="${r.condition === 'Overload' ? 'section' : 'node'}" data-id="${esc(r.worst)}"><td>${r.condition}</td><td>${r.phase}</td><td class="num">${r.count}</td><td>${esc(r.worst)}</td><td class="num">${r.value === null ? '' : fmt(r.value) + ' %'}</td></tr>`).join('')}</table>
      <p>Buses outside limits (${L.abnormal.limits_pct[0]} - ${L.abnormal.limits_pct[1]} %): <b>${L.abnormal.buses_outside_limits}</b></p>`,
    buses: () => tableReport(`${prefix} - Buses`, ['Node Id', 'VA (kVLN)', 'VB (kVLN)', 'VC (kVLN)', 'VA (%)', 'VB (%)', 'VC (%)'],
      Object.entries(L.nodes || {}).map(([id, v]) => ({ kind: 'node', id, cells: [id, ...'ABC'.split('').map((p) => v[p] ? fmt(v[p].kv, 3) : ''),
        ...'ABC'.split('').map((p) => v[p] ? `<span class="${v[p].pct < 95 || v[p].pct > 105 ? 'out' : ''}">${fmt(v[p].pct)}</span>` : '')] }))),
    lines: () => tableReport(`${prefix} - Lines and Cables`, ['Section Id', 'IA (A)', 'IB (A)', 'IC (A)', 'Loading (%)', 'Thru Power (kW)', 'Thru Power (kvar)', 'Total Loss (kW)'],
      Object.entries(L.sections || {}).map(([id, r]) => ({ kind: 'section', id, cells: [id, ...'ABC'.split('').map((p) => r.current_a[p] !== undefined ? fmt(r.current_a[p], 1) : ''),
        r.loading_pct === null ? '' : `<span class="${r.loading_pct > 100 ? 'out' : ''}">${fmt(r.loading_pct, 1)}</span>`, fmt(r.p_kw, 1), fmt(r.q_kvar, 1), fmt(r.loss_kw, 3)] }))),
    iterations: () => `<div class="rep-title">Iteration Report</div><table class="rep"><tr><th class="num">Iteration</th><th class="num">Max dV (pu)</th><th class="num">Detail</th></tr>
      ${L.iterations.map((it) => `<tr><td class="num">${it.iteration}</td><td class="num">${Number(it.max_dv_pu).toExponential(3)}</td><td class="num">${it.mismatch_pct !== undefined ? 'Meter mismatch ' + fmt(it.mismatch_pct, 3) + ' %' : it.max_current_mismatch_a !== undefined ? 'Mismatch ' + Number(it.max_current_mismatch_a).toExponential(2) + ' A' : 'J = ' + fmt(it.objective_before_step, 4)}</td></tr>`).join('')}</table>`,
    meters: () => {
      if (!L.meter_residuals) return '';
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
    loads: () => !L.loads ? '' : tableReport(L.analysis === 'allocate' ? `Load Allocation - Detailed (${L.method})` : 'Distribution State Estimator - Load Values',
      ['Load', 'Network', 'Initial kW', 'Initial kvar', L.analysis === 'allocate' ? 'Allocated kW' : 'Estimated kW', L.analysis === 'allocate' ? 'Allocated kvar' : 'Estimated kvar', 'Change (%)'],
      L.loads.map((r) => ({ cells: [r.id, r.network, fmt(r.initial_kw), fmt(r.initial_kvar), fmt(r.estimated_kw), fmt(r.estimated_kvar),
        r.initial_kw ? fmt(100 * (r.estimated_kw - r.initial_kw) / r.initial_kw, 1) : ''] }))),
  };
  body.innerHTML = (R[S.report] || R.messages)();
  const filter = $('#repFilter');
  if (filter) filter.addEventListener('input', () => {
    const q = filter.value.toLowerCase();
    $$('#reportBody table.rep tr[data-row]').forEach((tr) => { tr.hidden = q && !tr.dataset.row.toLowerCase().includes(q); });
  });
}

function tableReport(title, headers, rows) {
  const shown = rows.slice(0, 3000);
  return `<div class="rep-title">${esc(title)}</div><div class="filter"><input id="repFilter" placeholder="Filter..."> ${rows.length} rows</div>
    <table class="rep grid"><tr>${headers.map((h, i) => `<th class="${i ? 'num' : ''}">${h}</th>`).join('')}</tr>
    ${shown.map((r) => `<tr data-row="${esc(r.cells[0])}" class="${r.kind ? 'clickable' : ''}" ${r.kind ? `data-kind="${r.kind}" data-id="${esc(r.id)}"` : ''}>${r.cells.map((c, i) => `<td class="${i ? 'num' : ''}">${i ? c : esc(c)}</td>`).join('')}</tr>`).join('')}</table>`;
}

$('#reportTabs').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) { S.report = b.dataset.tab; renderReports(); } });
$('#reportBody').addEventListener('click', (e) => {
  const tr = e.target.closest('tr.clickable'); if (!tr || !tr.dataset.id) return;
  const hit = { kind: tr.dataset.kind, id: tr.dataset.id };
  if ((hit.kind === 'node' && S.nodes.has(hit.id)) || S.sectionById.has(hit.id)) { select(hit); zoomTo(hit); }
});

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
['colorLayer', 'tagLayer', 'showDevices'].forEach((id) => $('#' + id).addEventListener('change', draw));
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') select(null);
  if (e.key === 'F5') { e.preventDefault(); openAnalysis('loadflow'); }
});
new ResizeObserver(resize).observe($('#viewWrap'));
init();
