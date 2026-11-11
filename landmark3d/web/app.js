// Landmark3D local web app (vanilla JS, no build step)
const $app = document.getElementById('app');
const api = async (method, url, body, isForm) => {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    if (isForm) opt.body = body; else { opt.body = JSON.stringify(body); opt.headers['Content-Type'] = 'application/json'; }
  }
  const r = await fetch(url, opt);
  const t = await r.text();
  let d; try { d = t ? JSON.parse(t) : {}; } catch { d = { detail: t }; }
  if (!r.ok) throw new Error(d.detail || r.statusText);
  return d;
};
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const chip = s => `<span class="chip s-${esc(s)}">${esc(STATUS[s] || s)}</span>`;
const STATUS = { pending: 'chờ', running: 'đang chạy', done: 'xong', failed: 'lỗi', blocked: 'thiếu dữ liệu',
  waiting_approval: 'chờ duyệt', approved: 'đã duyệt', changes_requested: 'yêu cầu sửa' };
let pollTimer = null;

function route() {
  clearTimeout(pollTimer);
  const h = location.hash || '#/projects';
  const [, page, id] = h.split('/');
  if (page === 'project' && id) return viewProject(id);
  if (page === 'connections') return viewConnections();
  if (page === 'doctor') return viewDoctor();
  if (page === 'settings') return viewSettings();
  if (page === 'about') return viewAbout();
  return viewProjects();
}
window.addEventListener('hashchange', route);

// ---------------------------------------------------------------- T3: camera editor (Smart Texture)
// Each texture photo, undistorted, with the solved camera's model wireframe on top. The owner puts model corners
// where they really are in the photo; the camera is solved again from those points and the checks are shown.
const camState = {};

function renderCameras(id, d) {
  const box = document.getElementById('cameras');
  if (!box) return;
  if (!d.spec) { box.innerHTML = '<h2>Camera ảnh</h2><p class="muted">Cần có spec trước.</p>'; return; }
  box.innerHTML = `<h2>Camera ảnh <span class="muted">(Smart Texture: đặt góc nhà đúng chỗ trên ảnh)</span></h2>
    <p class="muted">Mỗi ảnh texture có một camera. Chọn một góc của mô hình, bấm vào đúng chỗ của góc đó trên ảnh, rồi
    bấm "Giải camera". Cần ít nhất 2 điểm, nên đặt 4–6 điểm ở các góc cách xa nhau.</p>
    <button id="cam-open">Mở trình chỉnh camera</button><div id="cam-body"></div>`;
  document.getElementById('cam-open').onclick = () => loadCameraList(id);
}

async function loadCameraList(id) {
  const body = document.getElementById('cam-body');
  body.innerHTML = '<p class="muted">Đang tải…</p>';
  let r;
  try { r = await api('GET', `/api/projects/${id}/cameras`); } catch (e) { body.innerHTML = `<p class="err">${esc(e.message)}</p>`; return; }
  if (!r.photos.length) { body.innerHTML = '<p class="muted">Không có ảnh nào được phép dùng làm texture.</p>'; return; }
  const tag = p => p.passes ? '<span class="okc">đạt</span>' : (p.status === 'none' ? 'chưa có' : esc(p.status));
  body.innerHTML = `<div class="row" id="cam-tabs">${r.photos.map(p => `<button data-k="${p.photo}">Ảnh ${p.photo} · ${tag(p)}${p.manual_points ? ` · ${p.manual_points} điểm tay` : ''}</button>`).join('')}</div>
    <div id="cam-editor"></div>`;
  body.querySelectorAll('#cam-tabs button').forEach(b => b.onclick = () => openCamera(id, +b.dataset.k));
  openCamera(id, r.photos[0].photo);
}

async function openCamera(id, k) {
  const el = document.getElementById('cam-editor');
  el.innerHTML = '<p class="muted">Đang tải ảnh…</p>';
  let v;
  try { v = await api('GET', `/api/projects/${id}/cameras/${k}`); } catch (e) { el.innerHTML = `<p class="err">${esc(e.message)}</p>`; return; }
  camState[id] = { k, v, t: Date.now(), points: Object.fromEntries(Object.entries(v.manual || {}).map(([a, b]) => [a, [...b]])),
                   active: Object.values(v.sketch_labels || {})[0] || Object.keys(v.labels)[0] };
  drawCamera(id);
}

function camGateText(v) {
  const m = v.metrics || {};
  const ok = v.status && ['solved', 'manual'].includes(v.status) && !(v.gate || []).length;
  const rows = [];
  if (m.rms_px != null) rows.push(`sai số điểm ${m.rms_px} px`);
  if (m.support != null) rows.push(`${Math.round(m.support * 100)}% góc mô hình khớp góc ảnh`);
  if (m.silhouette) rows.push(`đường viền trời ${m.silhouette.score}`);
  if ((m.off_points || []).length) rows.push(`điểm lệch: ${m.off_points.map(esc).join(', ')}`);
  const head = ok ? '<span class="okc">Camera đạt</span>'
    : v.status === 'failed' ? `<span class="err">Chưa giải được: ${esc(v.why || '')}</span>`
    : (v.status === 'skipped' || !v.status) ? '<span class="muted">Chưa có camera</span>' : '<span class="err">Camera chưa đạt</span>';
  return `<p>${head}${rows.length ? ' · ' + rows.join(' · ') : ''}</p>
    ${(v.gate || []).length ? `<ul class="muted">${v.gate.map(g => `<li>${esc(g)}</li>`).join('')}</ul>` : ''}`;
}

function drawCamera(id) {
  const st = camState[id], v = st.v;
  const [W, H] = v.size;
  const r = Math.max(4, W / 260), fs = Math.max(12, W / 90);
  const sketchIds = Object.values(v.sketch_labels || {});
  const order = [...sketchIds, ...Object.keys(v.labels).filter(k => !sketchIds.includes(k))];
  const num = Object.fromEntries(Object.entries(v.sketch_labels || {}).map(([n, kid]) => [kid, n]));
  const el = document.getElementById('cam-editor');
  const edges = (v.edges || []).map(e => `<line x1="${e[0]}" y1="${e[1]}" x2="${e[2]}" y2="${e[3]}" class="cam-edge" style="stroke-width:${r / 2}"/>`).join('');
  const proj = Object.entries(v.corners || {}).filter(([kid]) => !st.points[kid]).map(([kid, p]) =>
    `<circle cx="${p[0]}" cy="${p[1]}" r="${r}" class="cam-proj" data-kid="${esc(kid)}"><title>${esc(v.labels[kid] || kid)}</title></circle>`).join('');
  const man = Object.entries(st.points).map(([kid, p]) =>
    `<g><circle cx="${p[0]}" cy="${p[1]}" r="${r * 1.4}" class="cam-man${kid === st.active ? ' on' : ''}" data-kid="${esc(kid)}"><title>${esc(v.labels[kid] || kid)} (chuột phải: xoá)</title></circle>
     <text x="${p[0] + r * 1.8}" y="${p[1] - r * 1.8}" font-size="${fs}" class="cam-txt">${esc(num[kid] || '')}</text></g>`).join('');
  el.style.minHeight = `${el.offsetHeight}px`;               // redraws keep their height: the page does not jump
  el.innerHTML = `<div class="row" style="align-items:flex-start;margin-top:10px">
    <div class="grow" style="min-width:420px">
      <p style="margin:4px 0"><b>Ảnh thật — bấm vào đây để đặt góc đang chọn</b> <span class="muted">(${esc(v.labels[st.active] || st.active)})</span></p>
      <div class="cam-stage"><img src="/files/${id}/${esc(v.image)}" draggable="false">
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" id="cam-svg">${edges}${proj}${man}</svg></div>
      <p class="muted">Cyan: mô hình chiếu bằng camera hiện tại · hồng: điểm bạn đặt (vàng: góc đang chọn). Kéo điểm cyan hoặc hồng tới đúng góc; chuột phải để xoá điểm hồng.</p>
    </div>
    <div style="flex:0 0 360px">
      <label>Góc đang đặt</label>
      <select id="cam-active">${order.map(kid => `<option value="${esc(kid)}" ${kid === st.active ? 'selected' : ''}>${num[kid] ? num[kid] + ' — ' : ''}${esc(v.labels[kid] || kid)}${st.points[kid] ? ' ✓' : ''}</option>`).join('')}</select>
      <p class="muted">Bấm lên ảnh để đặt góc này. Số trên hình mô hình bên dưới giúp nhận ra góc.</p>
      <p class="muted" style="margin:6px 0 2px"><b>Hình mô hình</b> (chỉ để nhận ra góc số mấy — không bấm vào đây)</p>
      <img src="/files/${id}/${esc(v.sketch)}?t=${st.t}" draggable="false" style="width:100%;border:1px solid var(--line);border-radius:6px;opacity:.95">
      <p class="row"><button class="primary" id="cam-auto">AI tự đặt điểm</button>
        <button id="cam-solve">Giải camera (${Object.keys(st.points).length} điểm)</button>
        <button id="cam-clear">Xoá điểm</button></p>
      <p class="muted">"AI tự đặt điểm": AI tìm các góc trên ảnh, phóng to từng góc để chỉnh cho đúng, rồi giải camera
        (mất khoảng 1–2 phút). Sau đó bạn chỉ cần xem khung xanh có ôm sát công trình không; muốn thì kéo lại điểm.</p>
      <div id="cam-msg">${camGateText(v)}</div>
    </div></div>`;
  const img = el.querySelector('.cam-stage img');
  const done = () => { el.style.minHeight = ''; };
  if (img.complete) done(); else img.onload = done;
  bindCamera(id);
}

function bindCamera(id) {
  const st = camState[id];
  const svg = document.getElementById('cam-svg');
  const toImg = ev => {
    const pt = svg.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
    const q = pt.matrixTransform(svg.getScreenCTM().inverse());
    return [Math.round(q.x * 10) / 10, Math.round(q.y * 10) / 10];
  };
  const sel = document.getElementById('cam-active');
  sel.onchange = () => { st.active = sel.value; drawCamera(id); };
  let drag = null;
  svg.onpointerdown = ev => {
    if (ev.button === 2) return;
    const kid = ev.target.dataset ? ev.target.dataset.kid : null;
    if (kid) { drag = kid; st.active = kid; svg.setPointerCapture(ev.pointerId); ev.preventDefault(); return; }
    st.points[st.active] = toImg(ev);                        // place the chosen corner here, then go to the next one
    const opts = [...sel.options].map(o => o.value), i = opts.indexOf(st.active);
    const next = opts.slice(i + 1).find(k => !st.points[k]);
    if (next) st.active = next;
    drawCamera(id);
  };
  svg.onpointermove = ev => {
    if (!drag) return;
    st.points[drag] = toImg(ev);
    const c = svg.querySelector(`circle[data-kid="${CSS.escape(drag)}"]`);
    if (c) { c.setAttribute('cx', st.points[drag][0]); c.setAttribute('cy', st.points[drag][1]); }
  };
  svg.onpointerup = () => { if (drag) { drag = null; drawCamera(id); } };
  svg.oncontextmenu = ev => {
    ev.preventDefault();
    const kid = ev.target.dataset ? ev.target.dataset.kid : null;
    if (kid && st.points[kid]) { delete st.points[kid]; drawCamera(id); }
  };
  document.getElementById('cam-clear').onclick = async () => { st.points = {}; await solveCamera(id); };
  document.getElementById('cam-solve').onclick = () => solveCamera(id);
  document.getElementById('cam-auto').onclick = () => autoCamera(id);
}

async function autoCamera(id) {
  const st = camState[id];
  const msg = document.getElementById('cam-msg');
  document.querySelectorAll('#cam-editor button').forEach(b => b.disabled = true);
  msg.innerHTML = '<p class="muted">AI đang tìm và chỉnh các góc… (1–2 phút)</p>';
  try {
    st.v = await api('POST', `/api/projects/${id}/cameras/${st.k}/auto`);
    st.t = Date.now();
    st.points = Object.fromEntries(Object.entries(st.v.manual || {}).map(([a, b]) => [a, [...b]]));
    drawCamera(id);
    const extra = (st.v.dropped || []).length ? `<p class="muted">Đã bỏ ${st.v.dropped.length} điểm AI đặt lệch.</p>` : '';
    document.getElementById('cam-msg').insertAdjacentHTML('afterbegin', extra);
    const tab = document.querySelector(`#cam-tabs button[data-k="${st.k}"]`);
    const ok = !(st.v.gate || []).length && st.v.status === 'manual';
    if (tab) tab.innerHTML = `Ảnh ${st.k} · ${ok ? '<span class="okc">đạt</span>' : esc(st.v.status || '')} · AI`;
  } catch (e) {
    msg.innerHTML = `<p class="err">${esc(e.message)}</p>`;
    document.querySelectorAll('#cam-editor button').forEach(b => b.disabled = false);
  }
}

async function solveCamera(id) {
  const st = camState[id];
  const msg = document.getElementById('cam-msg');
  msg.innerHTML = '<p class="muted">Đang giải camera…</p>';
  try {
    st.v = await api('PUT', `/api/projects/${id}/cameras/${st.k}`, { points: st.points });
    st.t = Date.now();
    st.points = Object.fromEntries(Object.entries(st.v.manual || {}).map(([a, b]) => [a, [...b]]));
    drawCamera(id);
    const tab = document.querySelector(`#cam-tabs button[data-k="${st.k}"]`);
    const ok = !(st.v.gate || []).length && st.v.status === 'manual';
    const n = Object.keys(st.points).length;
    if (tab) tab.innerHTML = `Ảnh ${st.k} · ${ok ? '<span class="okc">đạt</span>' : esc(st.v.status || '')}${n ? ` · ${n} điểm tay` : ''}`;
  } catch (e) { msg.innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

// ---------------------------------------------------------------- projects
async function viewProjects() {
  const list = await api('GET', '/api/projects');
  $app.innerHTML = `
  <h1>Dự án</h1>
  <div class="card">
    <h2>Landmark mới</h2>
    <div class="row">
      <div class="grow"><label>Tên landmark</label><input id="n-name" placeholder="vd. Chợ Bến Thành"></div>
      <div class="grow"><label>Vị trí (thành phố, quốc gia)</label><input id="n-loc" placeholder="vd. TP. Hồ Chí Minh, Việt Nam"></div>
    </div>
    <details><summary class="muted">Tuỳ chọn: toạ độ, giới hạn lưới / dung lượng, ghi chú</summary>
      <div class="row">
        <div><label>Lat</label><input id="n-lat"></div><div><label>Lon</label><input id="n-lon"></div>
        <div><label>Tam giác tối đa</label><input id="n-tri" type="number" placeholder="50000"></div>
        <div><label>Dung lượng GLB tối đa (byte)</label><input id="n-bytes" type="number" placeholder="5000000"></div>
        <div><label>Nén</label><select id="n-comp"><option value="">mặc định</option><option>draco</option><option>none</option></select></div>
      </div>
      <label>Ghi chú cho AI (tuỳ chọn)</label><textarea id="n-notes" rows="2"></textarea>
    </details>
    <p><button class="primary" id="n-go">Tạo và bắt đầu</button> <span id="n-msg" class="muted"></span></p>
  </div>
  <div class="card"><h2>Danh sách</h2>
    <table><tr><th>Tên</th><th>Vị trí</th><th>Bước hiện tại</th><th></th></tr>
    ${list.map(p => `<tr><td>${esc(p.name)}</td><td>${esc(p.location)}</td><td>${esc(p.current || '—')} ${chip(p.status)}</td>
      <td><a href="#/project/${esc(p.id)}">Mở</a></td></tr>`).join('') || '<tr><td colspan=4 class="muted">Chưa có dự án</td></tr>'}
    </table></div>`;
  document.getElementById('n-go').onclick = async () => {
    const v = id => document.getElementById(id).value.trim();
    const budgets = {};
    if (v('n-tri')) budgets.triangles_max = +v('n-tri'), budgets.triangles_target = Math.round(+v('n-tri') * 0.8);
    if (v('n-bytes')) budgets.bytes_max = +v('n-bytes'), budgets.bytes_target = Math.round(+v('n-bytes') * 0.9);
    if (v('n-comp')) budgets.compression = v('n-comp');
    try {
      const r = await api('POST', '/api/projects', { name: v('n-name'), location: v('n-loc'),
        lat: v('n-lat') ? +v('n-lat') : null, lon: v('n-lon') ? +v('n-lon') : null, notes: v('n-notes'), budgets });
      await api('POST', `/api/projects/${r.id}/run`);
      location.hash = `#/project/${r.id}`;
    } catch (e) { document.getElementById('n-msg').textContent = e.message; }
  };
}

// ---------------------------------------------------------------- project
async function viewProject(id, fromPoll) {
  let d;
  try { d = await api('GET', `/api/projects/${id}`); } catch (e) { $app.innerHTML = `<p class="err">${esc(e.message)}</p>`; return; }
  const st = d.state;
  const cur = d.phases.find(p => !['done', 'approved'].includes(st.phases[p.id].status));
  const curStatus = cur ? st.phases[cur.id].status : 'complete';
  if (fromPoll && d.running && document.getElementById('phases')) {    // only what changes: the page stays where it is
    patchRunning(id, d, cur);
    pollTimer = setTimeout(() => viewProject(id, true), 2000);
    return;
  }
  const keepY = fromPoll ? window.scrollY : null;
  if (fromPoll) $app.style.minHeight = `${$app.offsetHeight}px`;       // the textures load later: no jump meanwhile
  $app.innerHTML = `
  <h1>${esc(st.name)} <span class="muted">— ${esc(st.location)}</span></h1>
  <div class="row" style="align-items:flex-start">
    <div class="card" style="flex:0 0 360px">
      <h2>Các bước</h2>
      <ul class="phases" id="phases">${phasesHtml(d)}</ul>
      <div id="reopen-box"></div>
      <p class="row"><button class="primary" id="run" ${d.running ? 'disabled' : ''}>${d.running ? 'Đang chạy…' : 'Chạy tiếp'}</button>
        <button id="budgets-btn">Giới hạn output</button>
        <button class="danger-outline" id="restart-open" ${d.running ? 'disabled' : ''}>Chạy lại từ đầu</button></p>
      <div id="budgets"></div>
      <div id="restart-box"></div>
    </div>
    <div class="grow" id="stage"></div>
  </div>
  <div class="card" id="satellite"></div>
  <div class="card" id="textures"></div>
  <div class="card" id="cameras"></div>
  <div class="card"><h2>Nhật ký <span class="muted">(mới nhất ở trên)</span></h2><pre class="log" id="log">${esc(logText(d))}</pre></div>`;
  Promise.resolve(renderTextures(id, d)).catch(() => {}).finally(() => {
    $app.style.minHeight = '';
    if (keepY !== null) window.scrollTo(0, keepY);
  });
  renderSatellite(id, d);
  renderCameras(id, d);
  if (!fromPoll) requestAnimationFrame(() => window.scrollTo(0, 0));
  else window.scrollTo(0, keepY);
  document.getElementById('run').onclick = async () => { await api('POST', `/api/projects/${id}/run`); viewProject(id); };
  document.getElementById('budgets-btn').onclick = () => renderBudgets(id, st.budgets);
  bindStageControls(id, d);
  const stage = document.getElementById('stage');
  if (!cur) stage.innerHTML = `<div class="card"><h2 class="okc">Hoàn tất</h2>${buildPanel(id, d)}</div>`;
  else if (curStatus === 'blocked') renderMissing(stage, id, st.missing || []);
  else if (curStatus === 'waiting_approval') renderApproval(stage, id, cur, d);
  else if (curStatus === 'failed') stage.innerHTML = `<div class="card"><h2 class="err">${esc(cur.title)}: lỗi</h2>
      <p>${esc(st.phases[cur.id].message)}</p>${d.doctor && cur.id === 'doctor' ? doctorTable(d.doctor) : ''}
      <button class="primary" id="retry">Thử lại bước này</button></div>`;
  else stage.innerHTML = `<div class="card"><h2>${esc(cur.title)}</h2><p class="muted">${d.running ? 'Đang chạy…' : 'Bấm "Chạy tiếp".'}</p></div>`;
  const retry = document.getElementById('retry');
  if (retry) retry.onclick = async () => { await api('POST', `/api/projects/${id}/reset/${cur.id}`); await api('POST', `/api/projects/${id}/run`); viewProject(id); };
  if (d.has_glb && (cur?.id === 'build_review' || cur?.id === 'release_review' || !cur)) mountViewer(`/files/${id}/output/model.glb?t=${Date.now()}`);
  if (cur?.id === 'spec_review' && d.spec) drawPlan(d.spec, d.spec_polys);
  if (d.running) pollTimer = setTimeout(() => viewProject(id, true), 2000);
}

const logText = d => [...d.events].reverse().map(e => `${e.t}  ${e.msg}${e.data ? '  ' + JSON.stringify(e.data).slice(0, 200) : ''}`).join('\n');

const phasesHtml = d => d.phases.map((p, i) => `<li><div class="grow">${esc(p.title)}<div class="msg">${esc(d.state.phases[p.id].message || '')}</div></div>
  <div class="phase-side">${chip(d.state.phases[p.id].status)}${canReopen(d, p, i) ? `<button class="mini" data-reopen="${esc(p.id)}">Quay lại</button>` : ''}</div></li>`).join('');

// While a run is going: new step states and log lines only. Someone reading older lines keeps their place.
function patchRunning(id, d, cur) {
  const list = document.getElementById('phases'), html = phasesHtml(d);
  if (list.dataset.html !== html) { list.innerHTML = html; list.dataset.html = html; }
  const log = document.getElementById('log'), text = logText(d);
  if (log.textContent !== text) {
    const top = log.scrollTop, before = log.scrollHeight;
    log.textContent = text;
    log.scrollTop = top > 0 ? top + log.scrollHeight - before : 0;
  }
  const stage = document.getElementById('stage'), key = cur ? cur.id : '';
  if (stage.dataset.running !== key) {
    stage.dataset.running = key;
    stage.innerHTML = `<div class="card"><h2>${esc(cur ? cur.title : 'Đang chạy')}</h2><p class="muted">Đang chạy…</p></div>`;
  }
}

// An approval stage can be reopened once the phase before it has finished.
const canReopen = (d, p, i) => p.approval && !d.running && d.state.phases[d.phases[i - 1].id].status === 'done'
  && d.state.phases[p.id].status !== 'waiting_approval';

const REOPEN_HELP = {
  sources_review: 'Bạn xem và chỉnh lại nguồn, ảnh. Khi duyệt lại A1, bước dựng khối sẽ chạy lại bằng AI và tạo model mới.',
  spec_review: 'Giữ nguyên kế hoạch khối hiện tại để bạn xem và sửa. Khi duyệt lại A2, tool build lại model từ kế hoạch đó, không dựng lại khối.',
  build_review: 'Giữ nguyên model hiện tại để bạn xem, đổi texture rồi build lại, hoặc yêu cầu sửa.',
  release_review: 'Giữ nguyên các file phát hành để bạn xem lại trước khi duyệt.',
};

function bindStageControls(id, d) {
  const reopenBox = document.getElementById('reopen-box'), restartBox = document.getElementById('restart-box');
  $app.querySelectorAll('[data-reopen]').forEach(b => b.onclick = () => {
    const p = d.phases.find(x => x.id === b.dataset.reopen);
    reopenBox.innerHTML = `<div class="inline-panel"><b>Quay lại ${esc(p.title)}</b>
      <p class="muted">${esc(REOPEN_HELP[p.id])} Các bước sau nó sẽ trở về trạng thái chờ.</p>
      <label>Lý do (tuỳ chọn)</label><input id="ro-notes">
      <p class="row"><button class="primary" id="ro-yes">Quay lại bước này</button><button id="ro-no">Huỷ</button><span id="ro-msg" class="err"></span></p></div>`;
    document.getElementById('ro-no').onclick = () => { reopenBox.innerHTML = ''; };
    document.getElementById('ro-yes').onclick = async () => {
      try { await api('POST', `/api/projects/${id}/reopen`, { phase: p.id, notes: document.getElementById('ro-notes').value.trim() }); viewProject(id); }
      catch (e) { document.getElementById('ro-msg').textContent = e.message; }
    };
  });
  document.getElementById('restart-open').onclick = () => {
    restartBox.innerHTML = `<div class="inline-panel"><b>Chạy lại từ đầu</b>
      <p class="muted">Tìm lại dữ liệu và dựng lại model, tốn thời gian và quota AI. Model hiện tại sẽ bị thay. File bạn tự tải lên được giữ;
      các yêu cầu sửa trước đó không còn áp dụng.</p>
      <label>Ghi chú cho AI</label><textarea id="rs-notes" rows="3" placeholder="vd. tháp đồng hồ cao hơn, có hai dãy nhà trong sân">${esc(d.state.notes || '')}</textarea>
      <details ${d.state.advanced_prompt ? 'open' : ''}><summary class="muted">Nâng cao</summary>
        <label>Prompt nâng cao (thêm vào mọi bước AI: nghiên cứu, dựng khối, so ảnh, mặt đứng; giám khảo độc lập không thấy)</label>
        <textarea id="rs-adv" rows="5">${esc(d.state.advanced_prompt || '')}</textarea>
        <label>Bắt đầu từ</label><select id="rs-start"><option value="research">Nghiên cứu (từ đầu)</option><option value="spec">Dựng khối (giữ dữ liệu đã tìm)</option></select>
      </details>
      <p class="row"><button class="danger" id="rs-yes">Xác nhận chạy lại</button><button id="rs-no">Huỷ</button><span id="rs-msg" class="err"></span></p></div>`;
    document.getElementById('rs-no').onclick = () => { restartBox.innerHTML = ''; };
    document.getElementById('rs-yes').onclick = async () => {
      const v = k => document.getElementById(k).value;
      try { await api('POST', `/api/projects/${id}/restart`, { notes: v('rs-notes'), advanced_prompt: v('rs-adv'), start: v('rs-start') }); viewProject(id); }
      catch (e) { document.getElementById('rs-msg').textContent = e.message; }
    };
  };
}

function renderSatellite(id, d) {
  const box = document.getElementById('satellite'); if (!box) return;
  const sats = d.sources.filter(s => s.kind === 'satellite');
  box.innerHTML = `<h2>Ảnh vệ tinh (chỉ tham khảo)</h2>
    <p class="muted">AI dùng ảnh vệ tinh (bắc ở trên, outline OSM màu vàng, lưới toạ độ theo mét) để đặt vị trí, kích thước và hướng
    các khối. Tự động: Esri World Imagery (ghi công trong release). Google Maps không được tải tự động vì điều khoản của Google;
    bạn có thể tải lên ảnh chụp màn hình của mình (bắc ở trên).</p>
    <div class="row">${sats.map(s => `<div><a href="/files/${id}/${esc(s.path)}" target="_blank"><img src="/files/${id}/${esc(s.path)}?t=${Date.now()}" style="max-width:420px;border:1px solid var(--line);border-radius:4px"></a>
      <div class="muted">${esc(s.title || '')}<br>${esc(s.author || '')}</div></div>`).join('') || '<p class="muted">Chưa có ảnh vệ tinh.</p>'}</div>
    <p class="row"><button id="sat-refresh">Tải lại từ Esri</button>
      <input type="file" id="sat-file" accept="image/*" style="width:auto"><button id="sat-up">Tải ảnh vệ tinh của tôi</button>
      <span id="sat-msg" class="muted"></span></p>`;
  const msg = t => document.getElementById('sat-msg').textContent = t;
  document.getElementById('sat-refresh').onclick = async () => {
    msg('đang tải…');
    try { await api('POST', `/api/projects/${id}/satellite/refresh`); viewProject(id); } catch (e) { msg(e.message); }
  };
  document.getElementById('sat-up').onclick = async () => {
    const f = document.getElementById('sat-file').files[0]; if (!f) { msg('Chọn ảnh trước'); return; }
    const fd = new FormData(); fd.append('file', f);
    try { await api('POST', `/api/projects/${id}/satellite`, fd, true); viewProject(id); } catch (e) { msg(e.message); }
  };
}

const LICENCES = [['owned', 'Ảnh của tôi'], ['permission_granted', 'Được phép dùng'], ['cc_by', 'CC BY'], ['cc_by_sa', 'CC BY-SA'],
  ['cc_by_nc', 'CC BY-NC (phi thương mại)'], ['public_domain', 'Public domain']];

async function renderTextures(id, d) {
  const box = document.getElementById('textures'); if (!box) return;
  const tx = (await api('GET', `/api/projects/${id}/textures`)).textures;
  const sp = d.spec;
  const mats = sp ? Object.keys(sp.materials).filter(m => m !== 'opening') : ['wall', 'roof', 'trim'];
  const parts = sp ? sp.parts.filter(p => p.enabled !== false).map(p => p.id) : [];
  const photos = d.sources.filter(s => s.kind === 'photo' && ['reference_and_texture', 'texture_permitted'].includes(s.use));
  const canRebuild = d.state.phases.spec_review.status === 'approved';
  box.innerHTML = `<h2>Texture</h2>
    <table><tr><th></th><th>Kiểu</th><th>Gán cho</th><th>Thông số</th><th>Bản quyền</th><th></th></tr>
    ${tx.map(t => `<tr><td><img class="thumb" src="/files/${id}/${esc(t.file)}"></td>
      <td>${t.mode === 'repeat' ? 'lặp' : 'dán ảnh'}</td>
      <td>${t.mode === 'repeat' ? `vật liệu <b>${esc(t.material)}</b>` : `khối <b>${esc(t.part)}</b>, mặt ${esc(t.side)}`}</td>
      <td class="muted">${t.mode === 'repeat' ? `${t.world_size_m} m/lần lặp` : `z ${t.z0 ?? 'đáy'}–${t.z1 ?? 'đỉnh tường'}`}${t.tint ? ` · tint rgb(${t.tint})` : ''}${t.crop ? ` · cắt ${t.crop}` : ''}</td>
      <td>${esc(t.licence || '')}<div class="muted">${esc(t.author || '')}</div></td>
      <td><button data-del="${esc(t.id)}">Xoá</button></td></tr>`).join('') || '<tr><td colspan=6 class="muted">Chưa có texture. Không có texture thì model dùng màu vật liệu.</td></tr>'}</table>
    <h3>Thêm texture</h3>
    <div class="row">
      <div class="grow"><label>Tải ảnh lên</label><input type="file" id="tx-file" accept="image/*"></div>
      <div class="grow"><label>…hoặc dùng ảnh đã có (bản quyền cho phép)</label><select id="tx-src"><option value="">—</option>
        ${photos.map(s => `<option value="${esc(s.id)}">${esc((s.title || s.path).slice(0, 60))} (${esc(s.licence)})</option>`).join('')}</select></div>
    </div>
    <div class="row">
      <div><label>Bản quyền (ảnh tải lên)</label><select id="tx-lic">${LICENCES.map(([v, l]) => `<option value="${v}">${l}</option>`).join('')}</select></div>
      <div class="grow"><label>Tác giả / nguồn</label><input id="tx-author"></div>
      <div><label>Kiểu</label><select id="tx-mode"><option value="repeat">Lặp trên vật liệu</option><option value="panel">Dán ảnh lên mặt khối</option></select></div>
    </div>
    <div class="row" id="tx-repeat">
      <div><label>Vật liệu</label><select id="tx-mat">${mats.map(m => `<option>${esc(m)}</option>`).join('')}</select></div>
      <div><label>Kích thước 1 lần lặp (m)</label><input id="tx-size" type="number" step="0.1" value="3"></div>
      <div><label>Màu nhân (tint)</label><input id="tx-tint" type="color" value="#ffffff"></div>
      <div><label><input type="checkbox" id="tx-notint" checked style="width:auto"> giữ nguyên màu ảnh</label></div>
    </div>
    <div class="row" id="tx-panel" style="display:none">
      <div><label>Khối</label><select id="tx-part">${parts.map(p => `<option>${esc(p)}</option>`).join('')}</select></div>
      <div><label>Mặt</label><select id="tx-side"><option value="south">Nam</option><option value="north">Bắc</option><option value="east">Đông</option><option value="west">Tây</option><option value="all">tất cả mặt</option></select></div>
      <div><label>Từ cao độ (m)</label><input id="tx-z0" type="number" step="0.1" placeholder="đáy"></div>
      <div><label>Đến cao độ (m)</label><input id="tx-z1" type="number" step="0.1" placeholder="đỉnh tường"></div>
      <div class="grow"><label>Cắt ảnh x0,y0,x1,y1 (0–1, góc trên trái)</label><input id="tx-crop" placeholder="vd. 0,0,0.33,1"></div>
    </div>
    <p class="row"><button class="primary" id="tx-add">Thêm texture</button>
      <button id="tx-rebuild" ${canRebuild ? '' : 'disabled title="Duyệt A2 trước"'}>Build lại với texture</button>
      <span id="tx-msg" class="muted"></span></p>`;
  const mode = document.getElementById('tx-mode');
  mode.onchange = () => { document.getElementById('tx-repeat').style.display = mode.value === 'repeat' ? '' : 'none';
                          document.getElementById('tx-panel').style.display = mode.value === 'panel' ? '' : 'none'; };
  box.querySelectorAll('[data-del]').forEach(b => b.onclick = async () => { await api('DELETE', `/api/projects/${id}/textures/${b.dataset.del}`); renderTextures(id, d); });
  document.getElementById('tx-add').onclick = async () => {
    const v = k => document.getElementById(k).value;
    const fd = new FormData();
    const f = document.getElementById('tx-file').files[0];
    if (f) fd.append('file', f);
    fd.append('source_id', v('tx-src')); fd.append('licence', v('tx-lic')); fd.append('author', v('tx-author'));
    fd.append('mode', v('tx-mode')); fd.append('material', v('tx-mat')); fd.append('world_size_m', v('tx-size') || '3');
    fd.append('tint', document.getElementById('tx-notint').checked ? '' : v('tx-tint'));
    fd.append('part', v('tx-part') || ''); fd.append('side', v('tx-side')); fd.append('z0', v('tx-z0')); fd.append('z1', v('tx-z1'));
    fd.append('crop', v('tx-crop'));
    try { await api('POST', `/api/projects/${id}/textures`, fd, true); document.getElementById('tx-msg').textContent = 'Đã thêm'; renderTextures(id, d); }
    catch (e) { document.getElementById('tx-msg').textContent = e.message; }
  };
  document.getElementById('tx-rebuild').onclick = async () => {
    try { await api('POST', `/api/projects/${id}/rebuild`); viewProject(id); }
    catch (e) { document.getElementById('tx-msg').textContent = e.message; }
  };
}

function renderBudgets(id, b) {
  const box = document.getElementById('budgets');
  const keys = ['triangles_max', 'triangles_target', 'bytes_max', 'bytes_target', 'materials_max', 'compression', 'texture_max_px'];
  box.innerHTML = keys.map(k => `<label>${k}</label><input data-k="${k}" value="${esc(b[k])}">`).join('') +
    `<p><button id="b-save">Lưu</button> <span class="muted">Áp dụng ở lần build tới</span></p>`;
  document.getElementById('b-save').onclick = async () => {
    const body = {};
    box.querySelectorAll('input').forEach(i => body[i.dataset.k] = i.dataset.k === 'compression' ? i.value : +i.value);
    await api('PUT', `/api/projects/${id}/budgets`, body); box.innerHTML = '<p class="okc">Đã lưu</p>';
  };
}

function renderMissing(stage, id, items) {
  stage.innerHTML = `<div class="card"><h2>Cần bạn bổ sung để qua cổng dữ liệu</h2>
    <p class="muted">Nguồn có cấu trúc và AI chưa tìm được các mục sau.</p>
    ${items.map(it => `<div class="card"><b>${esc(it.label)}</b><div class="muted">${esc(it.help)}</div>${fieldFor(it)}</div>`).join('')}
    <button class="primary" id="m-save">Gửi và chạy tiếp</button> <span id="m-msg" class="err"></span></div>`;
  document.getElementById('m-save').onclick = async () => {
    const values = {};
    for (const it of items) {
      if (it.kind === 'files') {
        const f = document.getElementById(`f-${it.id}`);
        for (const file of f.files) {
          const fd = new FormData(); fd.append('file', file);
          fd.append('licence', document.getElementById(`lic-${it.id}`).value);
          fd.append('author', document.getElementById(`aut-${it.id}`).value);
          await api('POST', `/api/projects/${id}/photos`, fd, true);
        }
      } else {
        const el = document.getElementById(`f-${it.id}`);
        if (el && el.value !== '') values[it.id] = it.kind === 'number' ? +el.value : el.value;
      }
    }
    try {
      const r = await api('POST', `/api/projects/${id}/inputs`, { values });
      if (r.missing.length) { document.getElementById('m-msg').textContent = 'Vẫn thiếu: ' + r.missing.map(m => m.label).join(', '); return; }
      await api('POST', `/api/projects/${id}/run`); viewProject(id);
    } catch (e) { document.getElementById('m-msg').textContent = e.message; }
  };
}

function fieldFor(it) {
  if (it.kind === 'select') return `<select id="f-${it.id}">${it.options.map(o => `<option ${o === it.default ? 'selected' : ''}>${o}</option>`).join('')}</select>`;
  if (it.kind === 'number') return `<input id="f-${it.id}" type="number" step="0.1" placeholder="${esc(it.unit || '')}">`;
  if (it.kind === 'files') return `<input id="f-${it.id}" type="file" accept="image/*" multiple>
      <div class="row"><div><label>Bản quyền</label><select id="lic-${it.id}">
      <option value="owned">Ảnh của tôi</option><option value="permission_granted">Được phép dùng</option>
      <option value="cc_by">CC BY</option><option value="cc_by_sa">CC BY-SA</option><option value="public_domain">Public domain</option>
      <option value="unknown">Không rõ (chỉ tham khảo)</option></select></div>
      <div class="grow"><label>Tác giả / nguồn</label><input id="aut-${it.id}"></div></div>`;
  return `<input id="f-${it.id}" placeholder="${it.kind === 'footprint' ? 'way/123456' : ''}">`;
}

function approvalButtons(phaseId) {
  return `<div class="card"><label>Ghi chú / yêu cầu sửa</label><textarea id="a-notes" rows="3"></textarea>
    <p class="row"><button class="ok" id="a-ok">Duyệt</button><button id="a-chg">Yêu cầu sửa</button></p></div>`;
}

function bindApproval(id, phaseId) {
  const go = async decision => {
    try {
      await api('POST', `/api/projects/${id}/approve`, { phase: phaseId, decision, notes: document.getElementById('a-notes').value });
    } catch (e) { alert(e.message); return; }
    await api('POST', `/api/projects/${id}/run`); viewProject(id);
  };
  document.getElementById('a-ok').onclick = () => go('approved');
  document.getElementById('a-chg').onclick = () => go('changes_requested');
}

function renderApproval(stage, id, cur, d) {
  let body = '';
  if (cur.id === 'sources_review') {
    const facts = d.research?.facts || {};
    body = `<h3>Thông tin tìm được</h3><table><tr><th>Mục</th><th>Giá trị</th><th>Độ tin cậy</th><th>Nguồn</th></tr>
      ${Object.entries(facts).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(JSON.stringify(v.value))}</td><td>${esc(v.confidence)}</td>
      <td class="muted">${esc(v.method || '')} ${v.source ? `<a href="${esc(v.source)}" target="_blank">nguồn</a>` : ''}</td></tr>`).join('') || '<tr><td colspan=4 class="muted">Không có</td></tr>'}</table>
      <h3>Nguồn và ảnh (bản quyền, cách dùng)</h3><table><tr><th></th><th>Nguồn</th><th>Bản quyền</th><th>Dùng làm</th><th>Texture cho</th></tr>
      ${d.sources.map(s => `<tr><td>${['photo', 'satellite'].includes(s.kind) ? `<img class="thumb" src="/files/${id}/${esc(s.path)}">` : esc(s.kind)}</td>
        <td>${esc(s.title || s.path || '')}<div class="muted">${esc(s.author || '')}</div>${s.url ? `<a href="${esc(s.url)}" target="_blank">link</a>` : ''}</td>
        <td>${esc(s.licence_short || s.licence)}</td>
        <td>${s.kind === 'photo' ? `<select data-sid="${esc(s.id)}" data-f="use">${['reference_and_texture', 'reference_only', 'texture_permitted', 'excluded'].map(u => `<option ${u === s.use ? 'selected' : ''}>${u}</option>`).join('')}</select>` : esc(s.use || '')}</td>
        <td>${s.kind === 'photo' ? `<select data-sid="${esc(s.id)}" data-f="texture_for">${['', 'wall', 'roof', 'trim'].map(u => `<option ${u === (s.texture_for || '') ? 'selected' : ''}>${u}</option>`).join('')}</select>` : ''}</td></tr>`).join('')}</table>`;
  } else if (cur.id === 'spec_review') {
    const sp = d.spec;
    body = `<p>Khung: ${sp.footprint.length_m} m × ${sp.footprint.width_m} m · ${sp.parts.length} khối · ước tính ~${sp.estimated_triangles} tam giác (chưa tính ô cửa)</p>
      <canvas class="plan" id="plan" width="520" height="400"></canvas>
      <table><tr><th>Khối</th><th>Đáy</th><th>Cao tường</th><th>Mái</th><th>Ghi chú</th></tr>${sp.parts.map(p => `<tr><td>${esc(p.id)} ${p.enabled === false ? '(tắt)' : ''}</td>
        <td>${p.base_z}</td><td>${p.height}</td><td>${esc(p.roof.type)} ${p.roof.height ?? ''}</td><td class="muted">${esc(p.note || '')}</td></tr>`).join('')}</table>
      ${(sp.issues || []).map(i => `<p class="${i.severity === 'error' ? 'err' : 'muted'}">${i.severity === 'error' ? 'LỖI — phải sửa trước khi duyệt' : 'Cảnh báo'}: ${esc(i.message)}</p>`).join('')}
      ${sp.defaults_used?.length ? `<p class="err">Giá trị mặc định (chưa có nguồn): ${sp.defaults_used.map(x => esc(x.key)).join(', ')}</p>` : ''}
      ${sp.notes?.length ? `<p class="muted">AI: ${sp.notes.map(esc).join(' · ')}</p>` : ''}
      ${sp.quality && sp.quality.scores ? `<h3>Chấm điểm độc lập (AI): ${esc(String(sp.quality.overall))}/10</h3>
        <p>${Object.entries(sp.quality.scores).map(([k, v]) => `${esc(k)} ${esc(String(v))}/5`).join(' · ')}</p>
        ${(sp.quality.errors || []).length ? `<ul>${sp.quality.errors.map(e => `<li>${esc(e)}</li>`).join('')}</ul>` : ''}` : ''}
      ${(sp.photo_review || []).length ? `<h3>So sánh ảnh ↔ model (AI tự rà soát)</h3>${sp.photo_review.map(r => `
        <p><b>${r.step === 'viewpoints' ? 'Nhận diện góc chụp (A–H)' : r.step === 'select' ? 'Chọn phương án tốt nhất' : r.step === 'facade' ? 'Mặt đứng & chi tiết (AI)' : 'Vòng ' + r.round}</b>: ${r.verdict === 'match' ? '<span class="okc">khớp</span>' : esc(r.verdict || r.result || '')}
        ${(r.differences || []).length ? `<ul>${r.differences.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}</p>
        <div class="row">${(r.images || []).map(im => `<a href="/files/${id}/${esc(im)}" target="_blank"><img src="/files/${id}/${esc(im)}" style="max-width:420px;border:1px solid var(--line);border-radius:4px"></a>`).join('')}</div>`).join('')}` : ''}
      ${(sp.observations || []).length ? `<h3>Quan sát mặt đứng (AI, từng ảnh)</h3><ul>${sp.observations.map(o => `<li>${esc(o.photo)}: ${esc(o.result)}${o.observations ? ` (${esc(String(o.observations))} quan sát)` : ''}</li>`).join('')}</ul>` : ''}
      <h3>Mặt đứng: chọn cạnh, sửa lưới, gán ảnh</h3><div id="facade-review"><p class="muted">Đang tải…</p></div>
      <details><summary>Sửa spec (JSON)</summary><textarea id="spec-json" rows="18">${esc(JSON.stringify(sp, null, 2))}</textarea>
      <button id="spec-save">Lưu spec</button> <span id="spec-msg"></span></details>`;
  } else {
    body = buildPanel(id, d);
  }
  stage.innerHTML = `<div class="card"><h2>${esc(cur.title)}</h2>${body}</div>${approvalButtons(cur.id)}`;
  stage.querySelectorAll('select[data-sid]').forEach(sel => sel.onchange = async () => {
    try { await api('PUT', `/api/projects/${id}/sources/${sel.dataset.sid}`, { [sel.dataset.f]: sel.value }); }
    catch (e) { alert(e.message); viewProject(id); }
  });
  const save = document.getElementById('spec-save');
  if (save) save.onclick = async () => {
    try { const r = await api('PUT', `/api/projects/${id}/spec`, { spec: JSON.parse(document.getElementById('spec-json').value) });
      document.getElementById('spec-msg').textContent = `Đã lưu (~${r.estimated_triangles} tam giác)`; viewProject(id); }
    catch (e) { document.getElementById('spec-msg').textContent = e.message; }
  };
  bindApproval(id, cur.id);
  if (cur.id === 'spec_review') loadFacadeReview(id);
}

// A2: facade step. Each photo is put on one wall (part + outline side); each photographed wall gets its own floor and
// bay grid, from the AI or from the lines typed here. The server validates everything and redoes the grids.
function parseLines(text) {
  const parts = String(text || '').split(',').map(x => x.trim()).filter(x => x !== '');
  if (!parts.length) return null;
  const nums = parts.map(Number);
  if (nums.some(n => !Number.isFinite(n))) throw new Error('Đường lưới phải là các số, cách nhau bằng dấu phẩy');
  return nums;
}

function sideLabel(s) { return `cạnh ${s.index}: ${s.length_m} m, hướng ${s.facing_deg}°`; }

async function loadFacadeReview(id) {
  const el = document.getElementById('facade-review');
  if (!el) return;
  let r;
  try { r = await api('GET', `/api/projects/${id}/observation_review`); }
  catch (e) { el.innerHTML = `<p class="err">${esc(e.message)}</p>`; return; }
  const wallOpts = (sel) => `<option value="">— chưa gán —</option>${r.parts.map(p =>
    p.sides.map(s => {
      const v = `${p.id}::${s.index}`;
      return `<option value="${esc(v)}" ${sel === v ? 'selected' : ''}>${esc(p.id)} · ${esc(sideLabel(s))}</option>`;
    }).join('')).join('')}`;
  const photos = r.photos.length ? r.photos.map(ph => {
    const sel = ph.part != null && ph.side != null ? `${ph.part}::${ph.side}` : '';
    const seen = ph.observations.length
      ? ph.observations.map(o => `${o.floors} tầng × ${o.bays} ô (${Math.round((o.confidence || 0) * 100)}%)`).join('; ')
      : 'chưa có quan sát';
    return `<tr><td>${esc(ph.path.split('/').pop())}</td>
      <td><select data-photo="${esc(ph.source_id || '')}" ${ph.source_id ? '' : 'disabled'}>${wallOpts(sel)}</select></td>
      <td class="muted">${esc(seen)}</td></tr>`;
  }).join('') : '<tr><td colspan="3" class="muted">Không có ảnh trong kế hoạch.</td></tr>';
  // Only walls that have a photo on them, or a grid already, need line fields: the rest would be noise.
  const assigned = new Set(r.photos.filter(p => p.part != null && p.side != null).map(p => `${p.part}::${p.side}`));
  const lineRows = r.parts.flatMap(p => p.sides.filter(s => assigned.has(`${p.id}::${s.index}`)).map(s => {
    const c = (p.correction.grids || []).find(g => g.side === s.index) || {};
    return `<div class="card" style="margin:6px 0"><b>${esc(p.id)}</b> · ${esc(sideLabel(s))}
      <div class="row"><label>Đường tầng (0–1, tăng dần) <input data-part="${esc(p.id)}" data-side="${s.index}" data-k="floor_lines" value="${esc((c.floor_lines || []).join(', '))}" placeholder="0, 0.25, 0.5, 0.75, 1"></label></div>
      <div class="row"><label>Đường ô (0–1, tăng dần) <input data-part="${esc(p.id)}" data-side="${s.index}" data-k="bay_lines" value="${esc((c.bay_lines || []).join(', '))}" placeholder="0, 0.2, 0.4, 0.6, 0.8, 1"></label></div></div>`;
  })).join('');
  const reports = r.parts.filter(p => p.observed_grid && (p.observed_grid.sides || []).length).map(p => {
    const lines = p.observed_grid.sides.map(s => s.applied
      ? `cạnh ${s.side}: ${s.floors} tầng × ${s.bays} ô (${s.side_chosen_by === 'person' ? 'người chọn' : 'mặc định'}, ${esc(s.provenance)})`
      : `cạnh ${s.side}: không áp (${esc(s.reason || s.policy || '')})`);
    return `<p class="muted"><b>${esc(p.id)}</b>: ${lines.join(' · ')}</p>`;
  }).join('');
  el.innerHTML = `
    ${r.ai_enabled ? '' : '<p class="muted">Đọc ảnh bằng AI đang tắt. Bật "Quan sát mặt đứng" trong Cài đặt để chạy.</p>'}
    <table><tr><th>Ảnh</th><th>Đặt lên mặt nào</th><th>Quan sát AI</th></tr>${photos}</table>
    ${lineRows || '<p class="muted">Gán ảnh lên một mặt để sửa đường lưới của mặt đó.</p>'}
    ${reports ? `<h3>Lưới đang dùng</h3>${reports}` : ''}
    <div class="row"><button id="fac-save">Lưu và áp lưới</button>
      <button id="fac-run" ${r.ai_enabled ? '' : 'disabled'}>Chạy AI đọc ảnh</button> <span id="fac-msg" class="muted"></span></div>`;
  const msg = (t, bad) => { const m = document.getElementById('fac-msg'); m.textContent = t; m.className = bad ? 'err' : 'muted'; };
  document.getElementById('fac-save').onclick = async () => {
    try {
      const photoParts = {}, photoSides = {};
      el.querySelectorAll('select[data-photo]').forEach(sel => {
        if (!sel.value || !sel.dataset.photo) return;
        const [pid, side] = sel.value.split('::');
        photoParts[sel.dataset.photo] = pid; photoSides[sel.dataset.photo] = Number(side);
      });
      const grids = {};
      el.querySelectorAll('input[data-part]').forEach(inp => {
        const key = `${inp.dataset.part}::${inp.dataset.side}`;
        const v = parseLines(inp.value);
        if (!v) return;
        grids[key] = grids[key] || { part: inp.dataset.part, side: Number(inp.dataset.side) };
        grids[key][inp.dataset.k] = v;
      });
      const partsDoc = {};
      Object.values(grids).forEach(g => {
        partsDoc[g.part] = partsDoc[g.part] || {};
        partsDoc[g.part].grids = partsDoc[g.part].grids || [];
        partsDoc[g.part].grids.push({ side: g.side, ...(g.floor_lines ? { floor_lines: g.floor_lines } : {}), ...(g.bay_lines ? { bay_lines: g.bay_lines } : {}) });
      });
      const res = await api('PUT', `/api/projects/${id}/observation_corrections`,
        { parts: partsDoc, photo_parts: photoParts, photo_sides: photoSides });
      const txt = (res.grid || []).map(p => p.applied
        ? `${p.part}: ${p.sides.filter(s => s.applied).map(s => `cạnh ${s.side} ${s.floors}×${s.bays}`).join(', ')}`
        : `${p.part}: không áp`);
      msg(`Đã lưu. ${txt.join(' · ')}. Duyệt lại A2 sau khi sửa.`, false);
      viewProject(id);
    } catch (e) { msg(e.message, true); }
  };
  document.getElementById('fac-run').onclick = async () => {
    msg('AI đang đọc ảnh… (có thể vài phút)', false);
    try {
      const res = await api('POST', `/api/projects/${id}/observations/run`, {});
      const ok = (res.log || []).filter(x => x.result === 'accepted').length;
      msg(`Đã đọc ${ok}/${(res.log || []).length} ảnh.${(res.errors || []).length ? ' Lỗi: ' + res.errors.join('; ') : ''}`, false);
      viewProject(id);
    } catch (e) { msg(e.message, true); }
  };
}

function buildPanel(id, d) {
  const b = d.build;
  if (!b) return '<p class="muted">Chưa build.</p>';
  return `<p>${b.ok ? '<span class="okc">Đạt</span>' : '<span class="err">Không đạt</span>'} · ${b.triangles} tam giác · ${b.bytes} byte ·
    ${b.materials} vật liệu · nén ${esc(b.compression)} · ${b.openings} ô cửa · ${b.panels ?? 0} mặt dán ảnh ·
    texture lặp: ${esc((b.textured_materials || []).join(', ') || 'không')} · render ${esc(b.render_engine)}</p>
    ${(b.errors || []).map(e => `<p class="err">${esc(e)}</p>`).join('')}${(b.warnings || []).map(e => `<p class="muted">${esc(e)}</p>`).join('')}
    ${b.accounting ? `<p class="muted">Tam giác theo loại: ${Object.entries(b.accounting.by_category).map(([k, v]) => `${esc(k)} ${esc(String(v))}`).join(' · ')}${b.accounting.target_utilization != null ? ` · ${Math.round(b.accounting.target_utilization * 100)}% mục tiêu` : ''}</p>` : ''}
    ${b.articulation && (b.articulation.placed || []).length ? `<p class="muted">Chi tiết kiến trúc đã đặt: ${esc(b.articulation.placed.join(', '))}</p>` : ''}
    ${b.articulation && (b.articulation.skipped || []).length ? `<p class="err">Bỏ qua: ${esc(b.articulation.skipped.join('; '))}</p>` : ''}
    <div id="viewer"></div>
    <p><a href="/files/${id}/output/model.glb" download>Tải GLB</a> · <a href="/files/${id}/blender/model.blend" download>.blend</a>
      ${d.state.phases.release.status === 'done' ? ` · <a href="/files/${id}/reports/release_notes.md" target="_blank">release notes</a>` : ''}</p>
    <div class="gallery">${d.renders.map(r => `<a href="/files/${id}/renders/${r}" target="_blank"><img src="/files/${id}/renders/${r}?t=${Date.now()}"></a>`).join('')}</div>`;
}

function drawPlan(sp, polys) {
  const c = document.getElementById('plan'); if (!c) return;
  const g = c.getContext('2d');
  const pts = sp.footprint.local;
  const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
  const pad = 20, w = c.width - 2 * pad, h = c.height - 2 * pad;
  const sx = Math.min(w / (Math.max(...xs) - Math.min(...xs) || 1), h / (Math.max(...ys) - Math.min(...ys) || 1)) * 0.95;
  const tx = x => pad + (x - Math.min(...xs)) * sx, ty = y => c.height - pad - (y - Math.min(...ys)) * sx;
  const poly = (ps, fill, stroke) => { g.beginPath(); ps.forEach((p, i) => i ? g.lineTo(tx(p[0]), ty(p[1])) : g.moveTo(tx(p[0]), ty(p[1]))); g.closePath(); g.fillStyle = fill; g.fill(); g.strokeStyle = stroke; g.stroke(); };
  poly(pts, '#eee6dc', '#555');
  const colors = ['#b0412e', '#2b5fa8', '#2f7d4f', '#a86b00', '#6b3fa0'];
  sp.parts.forEach((p, i) => {
    if (p.footprint === 'main' || p.enabled === false) return;
    let ps;
    const bad = (sp.issues || []).some(i => i.part === p.id && i.severity === 'error');
    if (polys && polys[p.id]) ps = polys[p.id];
    else if (p.footprint.polygon) ps = p.footprint.polygon;
    else if (p.footprint.rect) { const r = p.footprint.rect, a = (r.rot_deg || 0) * Math.PI / 180, u = [Math.cos(a), Math.sin(a)], v = [-u[1], u[0]];
      ps = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([su, sv]) => [r.cx + u[0] * su * r.w / 2 + v[0] * sv * r.d / 2, r.cy + u[1] * su * r.w / 2 + v[1] * sv * r.d / 2]); }
    else { const ci = p.footprint.circle; ps = [...Array(24)].map((_, k) => [ci.cx + ci.r * Math.cos(k * Math.PI / 12), ci.cy + ci.r * Math.sin(k * Math.PI / 12)]); }
    poly(ps, bad ? '#ff000044' : colors[i % colors.length] + '55', bad ? '#e00000' : colors[i % colors.length]);
    g.fillStyle = '#222'; g.fillText(p.id, tx(ps[0][0]), ty(ps[0][1]));
  });
  g.fillStyle = '#555'; g.fillText('N ↑', c.width - 40, 18);
}

async function mountViewer(url) {
  const el = document.getElementById('viewer'); if (!el) return;
  const THREE = await import('three');
  const { GLTFLoader } = await import('three/addons/loaders/GLTFLoader.js');
  const { DRACOLoader } = await import('three/addons/loaders/DRACOLoader.js');
  const { OrbitControls } = await import('three/addons/controls/OrbitControls.js');
  const r = new THREE.WebGLRenderer({ antialias: true }); r.setSize(el.clientWidth, el.clientHeight); el.appendChild(r.domElement);
  const { RoomEnvironment } = await import('three/addons/environments/RoomEnvironment.js');
  const scene = new THREE.Scene(); scene.background = new THREE.Color(0x3a3d42);
  // An environment map so glass and metal reflect something (same reason as the sky in the QA renders).
  const pmrem = new THREE.PMREMGenerator(r);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  r.toneMapping = THREE.ACESFilmicToneMapping; r.outputColorSpace = THREE.SRGBColorSpace;
  scene.add(new THREE.HemisphereLight(0xffffff, 0x444444, 1.2)); const sun = new THREE.DirectionalLight(0xffffff, 2); sun.position.set(1, 2, 1); scene.add(sun);
  const cam = new THREE.PerspectiveCamera(45, el.clientWidth / el.clientHeight, 0.5, 5000);
  const ctl = new OrbitControls(cam, r.domElement);
  const draco = new DRACOLoader(); draco.setDecoderPath(window.L3D_DRACO || 'https://unpkg.com/three@0.160.0/examples/jsm/libs/draco/');
  const loader = new GLTFLoader(); loader.setDRACOLoader(draco);
  loader.load(url, g => {
    scene.add(g.scene);
    const box = new THREE.Box3().setFromObject(g.scene), size = box.getSize(new THREE.Vector3()).length(), c = box.getCenter(new THREE.Vector3());
    cam.position.copy(c).add(new THREE.Vector3(size * 0.6, size * 0.45, size * 0.6)); ctl.target.copy(c); ctl.update();
  });
  (function loop() { if (!document.body.contains(el)) return; r.render(scene, cam); requestAnimationFrame(loop); })();
}

// ---------------------------------------------------------------- connections
async function viewConnections() {
  $app.innerHTML = '<h1>Kết nối AI</h1><p class="muted">Đang kiểm tra đăng nhập và biến môi trường…</p>';
  const [s, st] = await Promise.all([api('GET', '/api/settings'), api('GET', '/api/providers')]);
  const cliName = n => n === 'claude' ? 'Claude Code' : 'Codex';
  const prov = n => {
    const p = st[n], c = s.providers[n];
    const viaTxt = p.resolved === 'cli' ? `<span class="okc">đăng nhập web qua ${cliName(n)}</span>${p.login_method ? ' · ' + esc(p.login_method) : ''}${p.account ? ' · ' + esc(p.account) : ''}`
      : p.resolved === 'env' ? `<span class="okc">biến môi trường</span> ${esc(p.env_vars.join(', '))}`
      : '<span class="err">chưa kết nối</span>';
    const cliTxt = p.cli_path ? `${cliName(n)}: ${p.cli_logged_in ? '<span class="okc">đã đăng nhập</span>' : 'chưa đăng nhập'}` : `${cliName(n)} chưa cài`;
    const envTxt = p.env_vars.length ? esc(p.env_vars.join(', ')) : 'không có';
    const opt = (v, l) => `<option value="${v}" ${c.via === v ? 'selected' : ''}>${l}</option>`;
    return `<div class="card"><h2>${n === 'claude' ? 'Claude' : 'ChatGPT'}</h2>
    <p>Đang dùng: ${viaTxt}</p>
    <p class="muted">${cliTxt} · Biến môi trường có sẵn: ${envTxt}</p>
    <div class="row">
      <div><label>Cách kết nối</label><select id="via-${n}">${opt('auto', 'Tự chọn')}${opt('cli', 'Đăng nhập web (' + cliName(n) + ')')}${opt('env', 'Biến môi trường')}</select></div>
      <div class="grow"><label>Model khi đăng nhập web (để trống = mặc định của ${cliName(n)})</label><input id="cm-${n}" value="${esc(c.cli_model || '')}"></div>
      <div class="grow"><label>Model khi dùng biến môi trường</label><input id="m-${n}" value="${esc(c.model)}"></div>
    </div>
    <p class="row"><button class="primary" data-save="${n}">Lưu</button>
      ${p.cli_path ? `<button data-login="${n}">${p.cli_logged_in ? 'Đăng nhập lại' : 'Đăng nhập'} bằng trình duyệt</button>` : ''}
      <button data-test="${n}">Kiểm tra</button>
      <label><input type="checkbox" id="e-${n}" ${c.enabled ? 'checked' : ''} style="width:auto"> bật</label>
      <span id="r-${n}" class="muted"></span></p></div>`;
  };
  const opts = v => ['auto', 'claude', 'openai'].map(o => `<option ${o === v ? 'selected' : ''}>${o}</option>`).join('');
  $app.innerHTML = `<h1>Kết nối AI</h1><p class="muted">Tool không nhận và không lưu API key. Kết nối bằng tài khoản Claude / ChatGPT
    đăng nhập trên trình duyệt (qua Claude Code / Codex), hoặc bằng biến môi trường đã có sẵn trên máy. Gắn một hoặc cả hai;
    không có AI thì bạn bổ sung dữ liệu.</p>
    ${prov('claude')}${prov('openai')}
    <div class="card"><h2>Phân việc</h2><div class="row">
      <div><label>Tìm kiếm</label><select id="rt-research">${opts(s.routing.research)}</select></div>
      <div><label>Đọc ảnh</label><select id="rt-vision">${opts(s.routing.vision)}</select></div>
      <div><label>Dựng spec</label><select id="rt-spec">${opts(s.routing.spec)}</select></div>
      <div><label><input type="checkbox" id="xc" ${s.cross_check ? 'checked' : ''} style="width:auto"> Đối chiếu chéo khi có cả hai</label></div></div>
      <p><button id="rt-save">Lưu phân việc</button> <span id="rt-msg" class="muted"></span></p></div>`;
  $app.querySelectorAll('[data-save]').forEach(b => b.onclick = async () => {
    const n = b.dataset.save, v = k => document.getElementById(`${k}-${n}`).value.trim();
    try {
      await api('PUT', `/api/providers/${n}`, { via: v('via'), cli_model: v('cm'), model: v('m'), vision_model: v('m'),
                                                enabled: document.getElementById(`e-${n}`).checked });
      viewConnections();
    } catch (e) { document.getElementById(`r-${n}`).textContent = e.message; }
  });
  $app.querySelectorAll('[data-login]').forEach(b => b.onclick = async () => {
    const n = b.dataset.login;
    try {
      await api('POST', `/api/providers/${n}/login`);
      document.getElementById(`r-${n}`).textContent = 'Đã mở cửa sổ đăng nhập — hoàn tất trên trình duyệt rồi bấm Kiểm tra.';
    } catch (e) { document.getElementById(`r-${n}`).textContent = e.message; }
  });
  $app.querySelectorAll('[data-test]').forEach(b => b.onclick = async () => {
    const n = b.dataset.test; document.getElementById(`r-${n}`).textContent = 'đang thử…';
    const r = await api('POST', `/api/providers/${n}/test`);
    document.getElementById(`r-${n}`).textContent = r.ok ? `OK (${r.model}, ${r.via === 'cli' ? 'đăng nhập web' : 'biến môi trường'})` : `Lỗi: ${r.error || r.reply}`;
  });
  document.getElementById('rt-save').onclick = async () => {
    await api('PUT', '/api/settings', { routing: { research: document.getElementById('rt-research').value, vision: document.getElementById('rt-vision').value, spec: document.getElementById('rt-spec').value }, cross_check: document.getElementById('xc').checked });
    document.getElementById('rt-msg').textContent = 'Đã lưu';
  };
}

// ---------------------------------------------------------------- doctor / settings
function doctorTable(rep) {
  return `<table><tr><th></th><th>Kiểm tra</th><th>Chi tiết</th><th>Cách khắc phục</th></tr>${rep.checks.map(c => `<tr>
    <td>${c.ok ? '<span class="okc">✔</span>' : (c.optional ? '<span class="muted">○</span>' : '<span class="err">✖</span>')}</td>
    <td>${esc(c.id)}${c.optional ? ' <span class="muted">(tuỳ chọn)</span>' : ''}</td><td>${esc(c.detail)}</td><td class="muted">${esc(c.fix || '')}</td></tr>`).join('')}</table>`;
}

async function viewDoctor() {
  $app.innerHTML = '<h1>Môi trường</h1><p class="muted">Đang kiểm tra Blender, MCP, Node, AI…</p>';
  const rep = await api('GET', '/api/doctor');
  $app.innerHTML = `<h1>Môi trường ${rep.ok ? '<span class="okc">sẵn sàng</span>' : '<span class="err">chưa sẵn sàng</span>'}</h1>
    <div class="card">${doctorTable(rep)}<p><button id="iv">Cài glTF validator (npm)</button> <span id="iv-msg" class="muted"></span></p></div>`;
  document.getElementById('iv').onclick = async () => {
    document.getElementById('iv-msg').textContent = 'đang cài…';
    const r = await api('POST', '/api/doctor/install_validator');
    document.getElementById('iv-msg').textContent = r.ok ? 'xong' : r.detail;
  };
}

async function viewSettings() {
  const s = await api('GET', '/api/settings');
  const b = s.default_budgets;
  $app.innerHTML = `<h1>Cài đặt</h1><div class="card">
    <label>Thư mục dự án</label><input id="s-dir" value="${esc(s.projects_dir)}">
    <label>Đường dẫn Blender (để trống = tự tìm)</label><input id="s-bl" value="${esc(s.blender_path)}">
    <label>Liên hệ cho User-Agent (website hoặc e-mail của bạn) — Wikidata/Commons yêu cầu</label><input id="s-contact" value="${esc(s.contact)}">
    <h2>Giới hạn output mặc định</h2>
    ${Object.keys(b).map(k => `<label>${k}</label><input data-b="${k}" value="${esc(b[k])}">`).join('')}
    <p><button class="primary" id="s-save">Lưu</button> <span id="s-msg" class="muted"></span></p></div>`;
  document.getElementById('s-save').onclick = async () => {
    const db = {}; $app.querySelectorAll('[data-b]').forEach(i => db[i.dataset.b] = i.dataset.b === 'compression' ? i.value : +i.value);
    await api('PUT', '/api/settings', { projects_dir: document.getElementById('s-dir').value, blender_path: document.getElementById('s-bl').value,
      contact: document.getElementById('s-contact').value, default_budgets: db });
    document.getElementById('s-msg').textContent = 'Đã lưu';
  };
}

// ---------------------------------------------------------------- about
function publisherRows(pub) {
  const web = /^https?:\/\//i.test(pub.website || '') ? `<a href="${esc(pub.website)}" target="_blank" rel="noopener">${esc(pub.website)}</a>` : esc(pub.website);
  const rows = [['Đơn vị', esc(pub.name)], ['Website', web], ['E-mail', pub.email ? `<a href="mailto:${esc(pub.email)}">${esc(pub.email)}</a>` : ''],
    ['Điện thoại', esc(pub.phone)], ['Địa chỉ', esc(pub.address)]].filter(r => r[1]);
  return `<table class="kv">${rows.map(([k, v]) => `<tr><th>${k}</th><td>${v}</td></tr>`).join('')}</table>`;
}

async function viewAbout() {
  const a = await api('GET', '/api/about'), pub = a.publisher;
  const hasContact = pub.website || pub.email || pub.phone || pub.address;
  $app.innerHTML = `
  <div class="card about-hero">
    <img src="/static/persona-logo.png" alt="Logo ${esc(pub.name)}">
    <div><h1>${esc(a.product)}</h1><p class="tagline">${esc(a.tagline)}</p><p class="muted">Phiên bản ${esc(a.version)} · phát hành bởi ${esc(pub.name)}</p></div>
  </div>
  <div class="card"><h2>Giới thiệu</h2>
    <p>${esc(a.product)} tạo mô hình 3D của một công trình từ tên và vị trí. Tool tìm dữ liệu từ các nguồn mở và AI, dựng khối, so sánh với ảnh thật,
    build trong Blender và xuất file GLB. Bạn duyệt kết quả ở từng bước trước khi đi tiếp.</p>
    <ul>
      <li><b>A1 – Nguồn và bản quyền:</b> kiểm tra dữ liệu, ảnh tìm được và cách dùng.</li>
      <li><b>A2 – Mặt bằng và khối:</b> xem, sửa kế hoạch dựng hình và điểm giám khảo AI.</li>
      <li><b>A3 – Xem model:</b> xem model 3D, ảnh render và texture.</li>
      <li><b>A6 – Phát hành:</b> duyệt bộ file cuối cùng kèm ghi công nguồn.</li>
    </ul></div>
  <div class="card"><h2>Người phát hành và liên hệ</h2>${publisherRows(pub)}
    ${hasContact ? '' : '<p class="muted">Chưa có thông tin liên hệ.</p>'}</div>
  <div class="card"><h2>Nguồn dữ liệu và phần mềm</h2>
    <p class="muted">OpenStreetMap, Wikidata, Wikimedia Commons, ảnh vệ tinh Esri World Imagery (chỉ để tham khảo), Blender, three.js.
    Ảnh và dữ liệu thuộc bản quyền của chủ sở hữu; ghi công nằm trong release notes của từng dự án.</p></div>`;
}

api('GET', '/api/about').then(a => {
  document.getElementById('foot-text').textContent = `${a.product} ${a.version} · phát hành bởi ${a.publisher.name}`;
}).catch(() => {});

route();
