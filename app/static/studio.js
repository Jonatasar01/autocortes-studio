'use strict';
// AutoCortes Studio — editor manual. Usa a mesma API do editor clássico; o motor
// de edição (seleção, FFmpeg, legendas, direitos, tarefas) continua no servidor.

const $ = (id) => document.getElementById(id);
const state = {
  project: null, selected: null, format: '9:16', markIn: 0, markOut: 0, scale: 7,
  previewEnd: null, drag: null, framesKey: null, rulerKey: null, openVersion: 0,
  exporting: false, exportJob: null,
};
const CSRF = {'X-AutoCortes': '1'};
const ACTIVE_JOB = ['queued', 'running', 'cancelling'];
const FAILED_JOB = ['failed', 'cancelled', 'interrupted'];
const PROJECT_STATUS = {
  ready: 'Pronto', queued: 'Na fila', analyzing: 'Analisando', failed: 'Falhou',
  interrupted: 'Interrompido', cancelled: 'Cancelado',
};
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

const escape = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const fmt = (n) => {
  n = Math.max(0, Number(n) || 0);
  return String(Math.floor(n / 60)).padStart(2, '0') + ':' + (n % 60).toFixed(1).padStart(4, '0');
};
// A política de segurança (CSP style-src 'self') bloqueia atributos style em HTML
// gerado; posições são aplicadas pelo CSSOM, que é permitido.
function el(tag, className, px, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  for (const [k, v] of Object.entries(px || {})) node.style[k] = v + 'px';
  if (text != null) node.textContent = text;
  return node;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const scrollToEl = (el) => el?.scrollIntoView({behavior: reducedMotion.matches ? 'auto' : 'smooth', block: 'start'});

// ------------------------------------------------------------------ feedback

function toast(msg, error = false) {
  const box = $('toast');
  $('toastText').textContent = msg;
  box.className = 'toast' + (error ? ' error' : '');
  // Erros interrompem o leitor de tela; confirmações são anunciadas sem interromper.
  box.setAttribute('role', error ? 'alert' : 'status');
  box.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { box.hidden = true; }, error ? 8000 : 4000);
}
$('toastClose').onclick = () => { $('toast').hidden = true; };

function setStatus(text, tone = 'idle') {
  $('status').textContent = text;
  $('status').dataset.tone = tone;
}

async function api(url, method = 'GET', body) {
  let r;
  try {
    r = await fetch(url, {
      method,
      headers: {...(method === 'GET' ? {} : CSRF), ...(body ? {'Content-Type': 'application/json'} : {})},
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw Error('Sem conexão com o AutoCortes. Verifique se o aplicativo ainda está aberto.');
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw Error(typeof data.detail === 'string' ? data.detail : 'Erro ao executar a operação.');
  return data;
}

// ------------------------------------------------------------------ estado derivado

const current = () => state.project?.clips?.find((c) => c.id === state.selected);
const mediaDuration = () => state.project?.media?.duration || 0;
const isReady = () => state.project?.status === 'ready';
const video = $('video');

function selectionLabel() {
  const a = Math.min(state.markIn, state.markOut), b = Math.max(state.markIn, state.markOut);
  $('rangeLabel').textContent = state.project
    ? 'Seleção: ' + fmt(a) + ' — ' + fmt(b) + ' · duração ' + fmt(b - a)
    : 'Marque a entrada (I) e a saída (O) para criar um corte.';
  const band = $('selectionBand');
  band.hidden = !state.project || b - a <= 0;
  band.style.left = 74 + a * state.scale + 'px';
  band.style.width = (b - a) * state.scale + 'px';
}

function updateControls() {
  const ready = isReady(), c = current(), hasVideo = !!state.project;
  for (const id of ['back', 'play', 'forward', 'seek', 'markIn', 'markOut']) $(id).disabled = !hasVideo;
  $('add').disabled = !ready;
  $('split').disabled = !ready || !c;
  $('remove').disabled = !ready || !c;
  $('previewClip').disabled = !ready || !c;
  $('exportTop').disabled = state.exporting || !c || !ready;
  $('save').disabled = state.exporting || !c || !ready;
  $('exportTop').textContent = state.exporting ? 'Exportando…' : 'Exportar corte';
}

// ------------------------------------------------------------------ biblioteca e importação

async function loadProjects() {
  $('projects').setAttribute('aria-busy', 'true');
  try {
    const d = await api('/api/projects');
    $('projects').innerHTML = d.projects.map((p) => {
      const selected = p.id === state.project?.id;
      return '<button class="projectitem' + (selected ? ' selected' : '') + '" data-id="' + escape(p.id) + '"' +
        (selected ? ' aria-current="true"' : '') + '><strong>' + escape(p.name) + '</strong><small>' +
        '<span class="dot" data-status="' + escape(p.status) + '"></span>' + escape(PROJECT_STATUS[p.status] || p.status) +
        ' · ' + (p.duration ? fmt(p.duration) + ' · ' : '') + p.clips + (p.clips === 1 ? ' corte' : ' cortes') +
        '</small></button>';
    }).join('') || '<p class="empty">Nenhum projeto ainda. Importe um vídeo acima.</p>';
  } catch (e) {
    $('projects').innerHTML = '<p class="empty error">Não foi possível carregar os projetos.</p>';
    toast(e.message, true);
  } finally {
    $('projects').removeAttribute('aria-busy');
  }
}
$('projects').onclick = (e) => {
  const item = e.target.closest('.projectitem');
  if (item) openProject(item.dataset.id).catch((err) => toast(err.message, true));
};

const sourceMode = () => document.querySelector('[name=sourceMode]:checked').value;
function toggleMode() {
  const auto = $('mode').value === 'automatic';
  $('automaticOptions').hidden = !auto;
  $('importButton').textContent = auto ? 'Analisar e sugerir cortes' : 'Abrir no editor';
}
$('mode').onchange = toggleMode;
for (const r of document.querySelectorAll('[name=sourceMode]')) {
  r.onchange = () => {
    $('fileWrap').hidden = sourceMode() !== 'file';
    $('urlWrap').hidden = sourceMode() !== 'link';
  };
}
$('file').onchange = () => { $('filename').textContent = $('file').files[0]?.name || 'Nenhum arquivo selecionado'; };
$('newProject').onclick = () => {
  document.querySelector('[name=sourceMode][value=file]').click();
  $('file').click();
};

const drop = $('dropZone');
drop.ondragover = (e) => { e.preventDefault(); drop.classList.add('over'); };
drop.ondragleave = () => drop.classList.remove('over');
drop.ondrop = (e) => {
  e.preventDefault();
  drop.classList.remove('over');
  if (e.dataTransfer.files.length) {
    $('file').files = e.dataTransfer.files;
    $('file').onchange();
  }
};

function params() {
  return {
    source_rights: $('sourceRights').value, third_party_footage: $('thirdParty').checked ? 'yes' : 'unknown',
    external_audio: $('audioRights').value, graphics_rights: $('graphicsRights').value,
    monetization: $('monetization').value, license_notes: $('licenseNotes').value,
    editing_mode: $('mode').value, target_seconds: $('target').value, desired_count: $('desired').value,
    autopilot: 'false', recognize_music_opt_in: 'false',
  };
}

function uploadFile(f, p) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/projects?' + new URLSearchParams(p));
    xhr.setRequestHeader('X-AutoCortes', '1');
    xhr.setRequestHeader('X-Filename', encodeURIComponent(f.name));
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    $('uploadProgress').hidden = false;
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      $('uploadBar').value = Math.round(100 * e.loaded / e.total);
      $('uploadPercent').textContent = $('uploadBar').value + '%';
    };
    xhr.onload = () => {
      let d = {};
      try { d = JSON.parse(xhr.responseText); } catch { /* resposta vazia */ }
      if (xhr.status < 200 || xhr.status >= 300) reject(Error(d.detail || 'Erro no envio do vídeo.'));
      else resolve(d);
    };
    xhr.onerror = () => reject(Error('Falha de rede durante o envio.'));
    xhr.send(f);
  });
}

$('uploadForm').onsubmit = async (e) => {
  e.preventDefault();
  const f = $('file').files[0], p = params(), btn = $('importButton');
  if (sourceMode() === 'file' && !f) return toast('Selecione um arquivo de vídeo.', true);
  if (sourceMode() === 'link' && !$('videoUrl').value.trim()) return toast('Cole o link do vídeo.', true);
  btn.disabled = true;
  btn.setAttribute('aria-busy', 'true');
  try {
    if (sourceMode() === 'link') {
      setStatus('Baixando vídeo…', 'busy');
      const result = await api('/api/imports/link', 'POST', {url: $('videoUrl').value.trim(), options: p});
      toast('Importação adicionada à fila.');
      await waitImport(result.id);
    } else {
      setStatus('Enviando vídeo…', 'busy');
      const d = await uploadFile(f, p);
      await openProject(d.id);
    }
  } catch (err) {
    setStatus('Falha na importação', 'error');
    toast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.removeAttribute('aria-busy');
    $('uploadProgress').hidden = true;
  }
};

async function waitImport(id) {
  if (!id) return;
  for (let i = 0; i < 240; i++) {
    await sleep(1200);
    const j = await api('/api/jobs/' + id);
    if (j.status === 'done') return openProject(j.result.project_id);
    if (FAILED_JOB.includes(j.status)) throw Error(j.error || 'Importação interrompida.');
  }
  throw Error('A importação continua em segundo plano. Consulte a biblioteca em instantes.');
}

// ------------------------------------------------------------------ projeto

async function openProject(id) {
  if (!/^[a-f0-9]{32}$/.test(id || '')) return;
  const version = ++state.openVersion;
  setStatus('Carregando projeto…', 'busy');
  const p = await api('/api/projects/' + id);
  if (version !== state.openVersion) return;
  const changed = state.project?.id !== id;
  state.project = p;
  if (changed) {
    state.framesKey = state.rulerKey = null;
    state.previewEnd = null;
    state.selected = p.clips?.[0]?.id || null;
    state.markIn = 0;
    state.markOut = Math.min(30, mediaDuration());
    hideExport();
    computeScale();
    video.src = '/api/projects/' + id + '/source';
    history.replaceState({}, '', '/?projeto=' + id);
  } else if (!p.clips.some((c) => c.id === state.selected)) {
    state.selected = p.clips[0]?.id || null;
  }
  $('noVideo').hidden = true;
  $('projectName').textContent = p.name;
  $('projectMeta').textContent = fmt(mediaDuration()) + ' · ' + p.media.width + ' × ' + p.media.height + ' · ' +
    (p.editing_mode === 'manual' ? 'Edição manual' : 'Seleção automática');
  render();
  loadProjects();
  if (p.status === 'queued' || p.status === 'analyzing') {
    setStatus('Analisando vídeo…', 'busy');
    setTimeout(() => {
      if (state.openVersion === version) openProject(id).catch((e) => toast(e.message, true));
    }, 1700);
  } else if (p.status === 'ready') {
    setStatus('Projeto salvo', 'ok');
  } else {
    setStatus('Projeto indisponível', 'error');
    toast(p.error || 'O projeto não está pronto para edição.', true);
  }
}

async function refresh() {
  if (!state.project) return;
  const p = await api('/api/projects/' + state.project.id);
  state.project = p;
  if (!p.clips.some((c) => c.id === state.selected)) state.selected = p.clips[0]?.id || null;
  render();
  loadProjects();  // mantém a contagem de cortes da biblioteca atualizada
}

function clipBadges(c) {
  const out = [];
  out.push(c.selection_mode === 'manual' ? 'Manual' : 'Automático');
  if (c.score != null) out.push('Nota ' + Math.round(c.score));
  const exported = Object.keys(c.exports || {});
  if (exported.length) out.push('Exportado ' + exported.join(', '));
  return out;
}

function render() {
  const p = state.project;
  if (!p) return updateControls();
  const c = current();
  $('count').textContent = p.clips.length + (p.clips.length === 1 ? ' corte' : ' cortes');
  $('cutCards').innerHTML = p.clips.map((x) =>
    '<button class="cutCard' + (x.id === state.selected ? ' selected' : '') + '" data-id="' + x.id + '"' +
    (x.id === state.selected ? ' aria-current="true"' : '') + '><strong>' + escape(x.title) + '</strong><small>' +
    fmt(x.start) + ' — ' + fmt(x.end) + ' · ' + fmt(x.end - x.start) + '</small><span class="badges">' +
    clipBadges(x).map((b) => '<em>' + escape(b) + '</em>').join('') + '</span></button>').join('') ||
    '<p class="empty">Marque a entrada (I) e a saída (O) e clique em “Criar corte”.</p>';
  $('properties').hidden = !c;
  $('noSelection').hidden = !!c;
  if (c && !state.drag) {
    // Não sobrescreve o campo que o usuário está editando.
    for (const [id, value] of [['clipTitle', c.title], ['start', c.start], ['end', c.end]]) {
      if (document.activeElement !== $(id)) $(id).value = value;
    }
    $('clipInfo').textContent = 'Duração ' + fmt(c.end - c.start) + (c.reasons?.length ? ' · ' + c.reasons.slice(0, 2).join(' · ') : '');
  }
  selectionLabel();
  renderRights(p);
  renderTimeline();
  updateControls();
}

function renderRights(p) {
  const r = p.rights;
  if (!r) {
    $('rightsDetails').textContent = 'Sem revisão de direitos para este projeto.';
    return;
  }
  $('rightsDetails').innerHTML = '<p>Nível de risco: <b>' + escape(r.level || '—') + '</b></p><ul>' +
    (r.items || []).map((x) => '<li><b>' + escape(x.label) + ':</b> ' + escape(x.status_label) + '</li>').join('') + '</ul>';
}

async function loadAutomation() {
  try {
    const d = await api('/api/automation');
    const c = d.counts || {};
    $('automationDetails').innerHTML = '<p>' + (c.active || 0) + ' tarefa(s) ativa(s) · ' + (c.attention || 0) +
      ' precisando de atenção · ' + (c.paused || 0) + ' pausada(s) por direitos.</p>' +
      '<ul>' + (d.jobs || []).slice(0, 6).map((j) => '<li>' + escape(j.project_name) + ' — ' +
        escape(j.message || j.status) + '</li>').join('') + '</ul>';
  } catch {
    $('automationDetails').textContent = 'Painel de automação indisponível no momento.';
  }
}

function select(id, {seek = true} = {}) {
  state.selected = id;
  const c = current();
  if (c) {
    state.markIn = c.start;
    state.markOut = c.end;
    if (seek && state.project) video.currentTime = c.start;
  }
  render();
}

// ------------------------------------------------------------------ edição de cortes

async function create() {
  if (!isReady()) return toast('Abra um projeto pronto para editar.', true);
  const start = Math.min(state.markIn, state.markOut), end = Math.max(state.markIn, state.markOut);
  try {
    if (end - start < 1) throw Error('Marque um trecho de pelo menos 1 segundo com I e O.');
    const c = await api('/api/projects/' + state.project.id + '/clips', 'POST',
      {start, end, title: 'Corte ' + String(state.project.clips.length + 1).padStart(2, '0')});
    await refresh();
    select(c.id, {seek: false});
    toast('Corte criado.');
  } catch (e) { toast(e.message, true); }
}

async function save({silent = false} = {}) {
  const c = current();
  if (!c || !isReady()) return false;
  try {
    const start = Number($('start').value), end = Number($('end').value), title = $('clipTitle').value.trim();
    if (!title) throw Error('Dê um nome ao corte.');
    if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end - start < 1 || end > mediaDuration() + .02) {
      throw Error('Confira os tempos: o corte precisa ter pelo menos 1 segundo e terminar antes de ' + fmt(mediaDuration()) + '.');
    }
    if (title === c.title && start === c.start && end === c.end && silent) return true;
    const result = await api('/api/projects/' + state.project.id + '/clips/' + c.id, 'PATCH', {title, start, end});
    Object.assign(c, result);
    await refresh();
    if (!silent) toast('Alterações salvas.');
    return true;
  } catch (e) {
    toast(e.message, true);
    await refresh().catch(() => {});
    return false;
  }
}

async function remove() {
  const c = current();
  if (!c || !confirm('Excluir o corte “' + c.title + '”?')) return;
  try {
    await api('/api/projects/' + state.project.id + '/clips/' + c.id, 'DELETE');
    await refresh();
    toast('Corte excluído.');
  } catch (e) { toast(e.message, true); }
}

async function split() {
  const c = current();
  if (!c) return;
  const at = video.currentTime;
  if (at <= c.start + 1 || at >= c.end - 1) {
    return toast('Posicione o cursor dentro do corte, a pelo menos 1 segundo das bordas.', true);
  }
  try {
    const d = await api('/api/projects/' + state.project.id + '/clips/' + c.id + '/split', 'POST', {at});
    await refresh();
    select(d.second.id, {seek: false});
    toast('Corte dividido.');
  } catch (e) { toast(e.message, true); }
}

$('add').onclick = create;
$('split').onclick = split;
$('remove').onclick = remove;
$('properties').onsubmit = (e) => { e.preventDefault(); save(); };
$('cutCards').onclick = (e) => {
  const card = e.target.closest('.cutCard');
  if (card) select(Number(card.dataset.id));
};

// ------------------------------------------------------------------ linha do tempo

function renderTimeline() {
  const p = state.project;
  if (!p) return;
  const duration = Math.max(1, mediaDuration());
  const rulerKey = p.id + ':' + duration + ':' + state.scale;
  if (state.rulerKey !== rulerKey) {
    state.rulerKey = rulerKey;
    $('timelineCanvas').style.width = Math.max(580, duration * state.scale + 74 + 8) + 'px';
    const minGap = 70 / state.scale;  // mantém os rótulos a ~70 px de distância
    const step = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600].find((s) => s >= minGap) || 900;
    const ticks = [];
    for (let t = 0; t <= duration; t += step) ticks.push(el('span', 'tick', {left: t * state.scale}, fmt(t).replace(/\.0$/, '')));
    $('ruler').replaceChildren(...ticks);
  }
  $('clipSegments').replaceChildren(...p.clips.map((c) => {
    const b = el('button', 'segment' + (c.id === state.selected ? ' selected' : ''),
      {left: c.start * state.scale, width: Math.max(4, (c.end - c.start) * state.scale)});
    b.dataset.id = c.id;
    b.title = c.title + ' (' + fmt(c.start) + ' — ' + fmt(c.end) + ')';
    const left = el('i', 'handle left'), right = el('i', 'handle right');
    left.dataset.side = 'start';
    right.dataset.side = 'end';
    left.setAttribute('aria-hidden', 'true');
    right.setAttribute('aria-hidden', 'true');
    b.append(left, el('span', '', null, c.title), right);
    return b;
  }));
  updatePlayhead();
  renderFrames();
}

function renderFrames() {
  const p = state.project;
  const key = p.id + ':' + mediaDuration() + ':' + state.scale;
  if (state.framesKey === key) return;
  state.framesKey = key;
  const n = Math.min(42, Math.max(6, Math.ceil(mediaDuration() / 20)));
  const gap = Math.max(1, mediaDuration() / n);
  const frames = [];
  for (let i = 0; i < n; i++) frames.push(el('div', 'filmframe' + (i % 2 ? ' alt' : ''), {left: i * gap * state.scale, width: gap * state.scale}));
  $('frames').replaceChildren(...frames);
  $('wave').innerHTML = '<span class="track-info">Trilha de áudio · a forma de onda ainda não está disponível nesta versão</span>';
}

function updatePlayhead() {
  const t = video.currentTime || 0;
  $('playhead').style.left = 74 + t * state.scale + 'px';
  $('currentTime').textContent = fmt(t);
  $('duration').textContent = fmt(mediaDuration());
  if (!state.seeking) $('seek').value = mediaDuration() ? 1000 * t / mediaDuration() : 0;
}

// Zoom 1 = vídeo inteiro cabe na largura visível; cada nível amplia a partir daí.
function computeScale() {
  const visible = Math.max(200, $('timelineScroll').clientWidth - 74 - 8);
  const fit = mediaDuration() ? visible / mediaDuration() : 7;
  state.scale = Math.min(200, fit * Math.pow(1.6, Number($('zoom').value) - 1));
}

function setZoom(value) {
  $('zoom').value = Math.max(1, Math.min(8, value));
  computeScale();
  renderTimeline();
  selectionLabel();
}
let resizeTimer;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (state.project) setZoom(Number($('zoom').value)); }, 150);
});
$('zoom').oninput = () => setZoom(Number($('zoom').value));
$('zoomIn').onclick = () => setZoom(Number($('zoom').value) + 1);
$('zoomOut').onclick = () => setZoom(Number($('zoom').value) - 1);

$('clipSegments').onpointerdown = (e) => {
  const handle = e.target.closest('.handle'), btn = e.target.closest('.segment');
  if (!btn || !state.project) return;
  const c = state.project.clips.find((x) => x.id === Number(btn.dataset.id));
  if (!c) return;
  if (!handle) return select(c.id);
  e.preventDefault();
  state.selected = c.id;
  state.drag = {id: c.id, side: handle.dataset.side, original: {start: c.start, end: c.end}};
  btn.classList.add('dragging');
  handle.setPointerCapture(e.pointerId);
};
$('clipSegments').onpointermove = (e) => {
  if (!state.drag) return;
  const c = current();
  if (!c) return;
  const bounds = $('clipSegments').getBoundingClientRect();
  const t = Math.max(0, Math.min(mediaDuration(), (e.clientX - bounds.left) / state.scale));
  if (state.drag.side === 'start' && t <= c.end - 1) c.start = +t.toFixed(2);
  if (state.drag.side === 'end' && t >= c.start + 1) c.end = +t.toFixed(2);
  $('start').value = c.start;
  $('end').value = c.end;
  const btn = $('clipSegments').querySelector('[data-id="' + c.id + '"]');
  if (btn) {
    btn.style.left = c.start * state.scale + 'px';
    btn.style.width = (c.end - c.start) * state.scale + 'px';
  }
  state.markIn = c.start;
  state.markOut = c.end;
  video.currentTime = state.drag.side === 'start' ? c.start : c.end;
  selectionLabel();
};
$('clipSegments').onpointerup = async () => {
  const drag = state.drag;
  if (!drag) return;
  state.drag = null;
  const c = current();
  if (c && (c.start !== drag.original.start || c.end !== drag.original.end)) {
    if (await save({silent: true})) toast('Corte ajustado.');
  } else {
    render();
  }
};
$('clipSegments').onpointercancel = () => {
  const c = current(), drag = state.drag;
  state.drag = null;
  if (c && drag) Object.assign(c, drag.original);
  render();
};

function seekFromTrack(e) {
  if (!state.project) return;
  const b = e.currentTarget.getBoundingClientRect();
  video.currentTime = Math.min(mediaDuration(), Math.max(0, (e.clientX - b.left) / state.scale));
}
$('ruler').onclick = $('frames').onclick = $('wave').onclick = seekFromTrack;

// ------------------------------------------------------------------ player

video.ontimeupdate = () => {
  updatePlayhead();
  if (state.previewEnd !== null && video.currentTime >= state.previewEnd) {
    video.pause();
    state.previewEnd = null;
  }
};
video.onplay = () => { $('play').textContent = '❚❚'; $('play').setAttribute('aria-label', 'Pausar'); };
video.onpause = () => { $('play').textContent = '▶'; $('play').setAttribute('aria-label', 'Reproduzir'); };
video.onerror = () => {
  if (!state.project) return;
  setStatus('Vídeo indisponível', 'error');
  toast('O navegador não conseguiu reproduzir este vídeo. A edição e a exportação continuam funcionando; ' +
    'para pré-visualizar, converta o arquivo para MP4 (H.264).', true);
};
$('seek').onpointerdown = () => { state.seeking = true; };
$('seek').onpointerup = () => { state.seeking = false; };
$('seek').oninput = () => {
  if (state.project) video.currentTime = Number($('seek').value) / 1000 * mediaDuration();
};
const nudge = (s) => { video.currentTime = Math.max(0, Math.min(mediaDuration(), video.currentTime + s)); };
$('play').onclick = () => {
  if (!video.src) return;
  if (video.paused) video.play().catch(() => toast('Não foi possível reproduzir o vídeo.', true));
  else video.pause();
};
$('back').onclick = () => nudge(-1);
$('forward').onclick = () => nudge(1);
$('markIn').onclick = () => { state.markIn = video.currentTime; selectionLabel(); };
$('markOut').onclick = () => { state.markOut = video.currentTime; selectionLabel(); };
$('previewClip').onclick = () => {
  const c = current();
  if (!c) return;
  state.previewEnd = c.end;
  video.currentTime = c.start;
  video.play().catch(() => {});
};

document.addEventListener('keydown', (e) => {
  if (e.target.closest('input, textarea, select, [contenteditable]') || e.altKey || e.ctrlKey || e.metaKey) return;
  if (e.key === 'Escape') { $('toast').hidden = true; return; }
  if (!state.project) return;
  const k = e.key.toLowerCase();
  // Espaço sobre um botão focado continua acionando o botão.
  if (e.code === 'Space' && !e.target.closest('button, a')) { e.preventDefault(); $('play').click(); }
  else if (k === 'i') $('markIn').click();
  else if (k === 'o') $('markOut').click();
  else if (k === 'n') create();
  else if (k === 's' && current()) split();
  else if ((e.key === 'Delete' || e.key === 'Backspace') && current()) { e.preventDefault(); remove(); }
  else if (e.key === 'ArrowLeft' && !e.target.closest('input[type=range]')) { e.preventDefault(); nudge(e.shiftKey ? -5 : -1); }
  else if (e.key === 'ArrowRight' && !e.target.closest('input[type=range]')) { e.preventDefault(); nudge(e.shiftKey ? 5 : 1); }
});

for (const b of document.querySelectorAll('[data-format]')) {
  b.onclick = () => {
    state.format = b.dataset.format;
    for (const item of document.querySelectorAll('[data-format]')) {
      item.classList.toggle('active', item === b);
      item.setAttribute('aria-pressed', String(item === b));
    }
  };
}

// ------------------------------------------------------------------ exportação

function hideExport() {
  $('exportPanel').hidden = true;
  $('downloads').replaceChildren();
  $('jobStatus').textContent = '';
}

function showExport(text, {progress = null, cancellable = false, tone = ''} = {}) {
  $('exportPanel').hidden = false;
  $('exportPanel').dataset.tone = tone;
  $('jobStatus').textContent = text;
  $('exportBar').hidden = progress === null;
  if (progress !== null) $('exportBar').value = Math.round(progress * 100);
  $('cancelExport').hidden = !cancellable;
}

async function exportClip() {
  const c = current();
  if (!c || state.exporting || !isReady()) return toast('Selecione um corte pronto para exportar.', true);
  const pid = state.project.id, cid = c.id, format = state.format;
  state.exporting = true;
  updateControls();
  try {
    if (!await save({silent: true})) return;
    const rights = !!state.project.rights?.requires_acknowledgement;
    if (rights && !confirm('Este material tem alertas de direitos autorais. Confirma que pode utilizá-lo?')) return;
    $('downloads').replaceChildren();
    showExport('Enviando para a fila…', {progress: 0});
    setStatus('Exportando…', 'busy');
    const j = await api('/api/projects/' + pid + '/clips/' + cid + '/export', 'POST', {
      format, framing: $('framing').value, subtitles: $('subtitles').checked,
      normalize_audio: $('normalize').checked, hook: $('hook').checked, acknowledge_rights: rights,
    });
    state.exportJob = j.id;
    await monitorJob(j.id, pid, cid, format);
  } catch (e) {
    if (state.project?.id === pid) showExport(e.message, {tone: 'error'});
    setStatus('Exportação não concluída', 'error');
    toast(e.message, true);
  } finally {
    state.exporting = false;
    state.exportJob = null;
    updateControls();
  }
}

async function monitorJob(id, pid, cid, format) {
  for (let i = 0; i < 2400; i++) {
    const j = await api('/api/jobs/' + id);
    const here = state.project?.id === pid;
    if (here && ACTIVE_JOB.includes(j.status)) {
      showExport((j.message || 'Processando') + ' · ' + Math.round((j.progress || 0) * 100) + '%',
        {progress: j.progress || 0, cancellable: j.status !== 'cancelling'});
    }
    if (j.status === 'done') {
      setStatus('Exportação concluída', 'ok');
      if (!here) return toast('Exportação concluída em outro projeto.');
      await refresh();
      showExport('Exportação concluída.', {tone: 'ok'});
      const a = document.createElement('a');
      a.className = 'primary full download-link';
      a.href = '/api/projects/' + pid + '/clips/' + cid + '/download?format=' + encodeURIComponent(format);
      a.textContent = 'Baixar MP4 (' + format + ')';
      $('downloads').replaceChildren(a);
      for (const w of j.result?.warnings || []) {
        const p = document.createElement('p');
        p.className = 'hint';
        p.textContent = w;
        $('downloads').append(p);
      }
      toast('Seu vídeo está pronto.');
      return;
    }
    if (j.status === 'cancelled') {
      if (here) showExport('Exportação cancelada. Nenhum arquivo parcial foi mantido.', {tone: 'muted'});
      setStatus('Exportação cancelada', 'idle');
      return;
    }
    if (FAILED_JOB.includes(j.status)) throw Error(j.error || 'A exportação falhou.');
    await sleep(i < 20 ? 700 : 1500);
  }
  throw Error('O acompanhamento expirou; a tarefa continua no histórico do projeto.');
}

$('cancelExport').onclick = async () => {
  if (!state.exportJob) return;
  $('cancelExport').hidden = true;
  showExport('Cancelando…', {progress: Number($('exportBar').value) / 100});
  try {
    await api('/api/jobs/' + state.exportJob + '/cancel', 'POST');
  } catch (e) { toast(e.message, true); }
};
$('exportTop').onclick = exportClip;

// ------------------------------------------------------------------ navegação

const VIEWS = {editor: 'workspace', media: 'library', cuts: 'cutlist', automation: 'advanced', rights: 'advanced'};
for (const b of document.querySelectorAll('[data-view]')) {
  b.onclick = () => {
    for (const x of document.querySelectorAll('[data-view]')) {
      x.classList.toggle('active', x === b);
      if (x === b) x.setAttribute('aria-current', 'true'); else x.removeAttribute('aria-current');
    }
    const target = $(VIEWS[b.dataset.view]);
    if (target.tagName === 'DETAILS') target.open = true;
    if (b.dataset.view === 'automation') loadAutomation();
    scrollToEl(target);
  };
}
$('refresh').onclick = loadProjects;
$('advanced').ontoggle = () => { if ($('advanced').open) loadAutomation(); };

loadProjects();
updateControls();
const initial = new URLSearchParams(location.search).get('projeto');
if (initial) openProject(initial).catch((e) => { setStatus('Projeto não encontrado', 'error'); toast(e.message, true); });
