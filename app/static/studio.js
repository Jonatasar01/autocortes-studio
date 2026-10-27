'use strict';
// AutoCortes Studio — editor manual. Usa a mesma API do editor clássico; o motor
// de edição (seleção, FFmpeg, legendas, direitos, tarefas) continua no servidor.

const $ = (id) => document.getElementById(id);
const state = {
  project: null, selected: null, format: '9:16', markIn: 0, markOut: 0, scale: 7,
  previewEnd: null, drag: null, stripKey: null, stripToken: 0, rulerKey: null, openVersion: 0,
  exporting: false, exportJob: null, edited: {}, syncedClip: null, dirty: false, seeking: false,
};
const CSRF = {'X-AutoCortes': '1'};
const ACTIVE_JOB = ['queued', 'running', 'cancelling'];
const FAILED_JOB = ['failed', 'cancelled', 'interrupted'];
const PROJECT_STATUS = {
  ready: 'Pronto', queued: 'Na fila', analyzing: 'Analisando', failed: 'Falhou',
  interrupted: 'Interrompido', cancelled: 'Cancelado',
};
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
const narrow = window.matchMedia('(max-width: 1180px)');
const phone = window.matchMedia('(max-width: 860px)');
const EXPORT_IDLE = 'Selecione um corte e clique em Exportar.';

const escape = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
// mm:ss.d para rótulos; mm:ss.mmm para os campos de início e fim. A partir de 1 h: h:mm:ss (lives longas
// mostravam "495:00").
const fmt = (n) => {
  n = Math.max(0, Number(n) || 0);
  const h = Math.floor(n / 3600), m = Math.floor(n / 60) % 60;
  const sec = (n % 60).toFixed(1).padStart(4, '0');
  return h ? h + ':' + String(m).padStart(2, '0') + ':' + sec : String(Math.floor(n / 60)).padStart(2, '0') + ':' + sec;
};
const fmtMs = (n) => {
  n = Math.max(0, Math.round((Number(n) || 0) * 1000));
  const ms = n % 1000, s = Math.floor(n / 1000) % 60, m = Math.floor(n / 60000) % 60, h = Math.floor(n / 3600000);
  const tail = String(s).padStart(2, '0') + '.' + String(ms).padStart(3, '0');
  return h ? h + ':' + String(m).padStart(2, '0') + ':' + tail : String(m).padStart(2, '0') + ':' + tail;
};
// Rótulo da régua: sem décimos e com horas quando o vídeo passa de 1 h.
const fmtTick = (t, long) => {
  const h = Math.floor(t / 3600), m = Math.floor(t / 60) % 60, s = Math.round(t % 60);
  return (long ? h + ':' + String(m).padStart(2, '0') : String(Math.floor(t / 60)).padStart(2, '0')) + ':' +
    String(s).padStart(2, '0');
};
// Aceita 75.3, 1:15.3, 01:15.300 e 1:02:03.
function parseTime(text) {
  const parts = String(text).trim().replace(',', '.').split(':');
  if (!parts[0] || parts.length > 3 || parts.some((p) => !/^\d+(\.\d+)?$/.test(p))) return NaN;
  return Math.round(parts.reduce((acc, p) => acc * 60 + Number(p), 0) * 1000) / 1000;
}
const fmtSize = (b) => (b >= 1e9 ? (b / 1e9).toFixed(1) + ' GB' : Math.max(1, Math.round(b / 1e6)) + ' MB');
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
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* armazenamento indisponível */ } },
};
const setFill = (input) => {
  const span = Number(input.max) - Number(input.min) || 1;
  input.style.setProperty('--fill', (100 * (Number(input.value) - Number(input.min)) / span) + '%');
};

// ------------------------------------------------------------------ feedback

function toast(msg, error = false) {
  const box = $('toast');
  $('toastText').textContent = msg;
  box.className = 'toast' + (error ? ' error' : '');
  box.querySelector('.toasticon use').setAttribute('href', error ? '#i-alert' : '#i-check');
  // Erros interrompem o leitor de tela; confirmações são anunciadas sem interromper.
  box.setAttribute('role', error ? 'alert' : 'status');
  box.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { box.hidden = true; }, error ? 8000 : 4000);
}
$('toastClose').onclick = () => { $('toast').hidden = true; };

function setStatus(text, tone = 'idle') {
  $('statusText').textContent = text;
  $('status').dataset.tone = tone;
}

function setLoading(text, progress = null, steps = '') {
  $('loading').hidden = !text;
  if (text) $('loadingText').textContent = text;
  $('loadingBar').hidden = !text || progress === null;
  if (progress !== null) $('loadingBar').value = Math.round(progress * 100);
  $('loadingSteps').hidden = !text || !steps;
  $('loadingSteps').textContent = steps || '';
}

// Andamento da análise: etapa atual, porcentagem, tempo restante estimado e quanto levou cada etapa.
const STAGE_NAMES = {validando: 'Validação', musica: 'Música', extraindo_audio: 'Áudio', transcrevendo: 'Transcrição',
  cenas: 'Cenas', selecionando: 'Seleção', ia_local: 'IA local', curadoria_ia: 'Curadoria IA', fila: 'Fila'};
// Faixa da barra geral ocupada pelas etapas com progresso real (as outras são rápidas ou sem medida).
const STAGE_SPANS = {extraindo_audio: [0.08, 0.14], transcrevendo: [0.14, 0.74], cenas: [0.75, 0.88]};
const fmtDur = (s) => {
  s = Math.max(0, Math.round(s));
  if (s < 60) return s + ' s';
  if (s < 3600) return Math.floor(s / 60) + ' min' + (s < 600 && s % 60 ? ' ' + (s % 60) + ' s' : '');
  return Math.floor(s / 3600) + ' h ' + Math.floor((s % 3600) / 60) + ' min';
};
function analysisLabel(job, events = []) {
  if (!job) return {text: 'Analisando vídeo…', progress: null, pct: null, steps: ''};
  const now = Date.now() / 1000;
  const pct = Math.round((job.progress || 0) * 100);
  const stage = job.status === 'queued' ? 'Na fila' : (job.message || 'Analisando vídeo');
  // Tempo restante da etapa atual pela velocidade dela até agora (transcrição, cenas, extração de áudio).
  let eta = null;
  const span = STAGE_SPANS[job.stage];
  const begun = [...events].reverse().find((e) => e.stage === job.stage)?.ts;
  if (span && begun) {
    const done = ((job.progress || 0) - span[0]) / (span[1] - span[0]);
    const elapsed = now - begun;
    if (done > 0.03 && elapsed > 20) eta = elapsed * (1 - done) / done;
  }
  const total = job.started ? now - job.started : 0;
  const text = stage + ' · ' + pct + '%' + (eta != null ? ' · ≈ ' + fmtDur(eta) + ' restantes'
    : total >= 60 ? ' · ' + fmtDur(total) : '');
  // Etapas concluídas com a duração de cada uma; a atual com ▸.
  const marks = [];
  for (let i = 0; i < events.length; i++) {
    const e = events[i];
    if (!STAGE_NAMES[e.stage] || (marks.length && marks[marks.length - 1].stage === e.stage)) continue;
    marks.push({stage: e.stage, ts: e.ts});
  }
  const steps = marks.map((m, i) => {
    const end = marks[i + 1]?.ts;
    return end ? '✓ ' + STAGE_NAMES[m.stage] + ' ' + fmtDur(end - m.ts) : '▸ ' + STAGE_NAMES[m.stage] + ' ' + fmtDur(now - m.ts);
  }).join('  ·  ');
  return {text, progress: job.progress || 0, pct, steps};
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

// ------------------------------------------------------------------ diálogos

const dialogs = document.querySelectorAll('dialog');
for (const d of dialogs) {
  d.addEventListener('click', (e) => { if (e.target === d) d.close(); });
  for (const b of d.querySelectorAll('[data-close]')) b.onclick = () => d.close();
}
const anyDialogOpen = () => !!document.querySelector('dialog[open]');

function askConfirm({title, text, yes = 'Confirmar', danger = false}) {
  const dlg = $('confirmDialog');
  $('confirmTitle').textContent = title;
  $('confirmText').textContent = text;
  $('confirmYes').textContent = yes;
  $('confirmYes').classList.toggle('danger', danger);
  return new Promise((resolve) => {
    // Os botões respondem direto; o evento close cobre Esc e clique fora do diálogo.
    let answered = false;
    const finish = (value) => { if (!answered) { answered = true; resolve(value); } };
    $('confirmYes').onclick = () => { dlg.close('yes'); finish(true); };
    $('confirmNo').onclick = () => { dlg.close('no'); finish(false); };
    dlg.onclose = () => finish(dlg.returnValue === 'yes');
    dlg.returnValue = 'no';
    dlg.showModal();
    $('confirmNo').focus();
  });
}

// ------------------------------------------------------------------ estado derivado

const current = () => state.project?.clips?.find((c) => c.id === state.selected);
const mediaDuration = () => state.project?.media?.duration || 0;
const isReady = () => state.project?.status === 'ready';
const video = $('video');

// Largura da coluna de rótulos da linha do tempo; vem do CSS (--label) e muda por breakpoint.
let LABEL = 112;
const readLabel = () => {
  LABEL = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--label')) || 112;
};

function selectionLabel() {
  const a = Math.min(state.markIn, state.markOut), b = Math.max(state.markIn, state.markOut);
  $('rangeLabel').textContent = state.project
    ? 'Seleção ' + fmt(a) + ' — ' + fmt(b) + ' · ' + fmt(b - a)
    : 'Marque a entrada (I) e a saída (O).';
  const band = $('selectionBand');
  band.hidden = !state.project || b - a <= 0;
  band.style.left = a * state.scale + 'px';
  band.style.width = (b - a) * state.scale + 'px';
}

function updateControls() {
  const ready = isReady(), c = current(), hasVideo = !!state.project;
  for (const id of ['back', 'play', 'forward', 'seek', 'markIn', 'markOut', 'mute', 'fullscreen']) $(id).disabled = !hasVideo;
  $('add').disabled = !ready;
  $('split').disabled = !ready || !c;
  $('remove').disabled = !ready || !c;
  $('removeSide').disabled = !ready || !c;
  $('previewClip').disabled = !ready || !c;
  $('exportTop').disabled = state.exporting || !c || !ready;
  $('save').disabled = state.exporting || !c || !ready;
  $('saveTop').disabled = state.exporting || !c || !ready;
  $('exportLabel').textContent = state.exporting === true ? 'Exportando…' : 'Exportar';
  $('previewTop').disabled = state.exporting || !c || !ready;
  $('previewLabel').textContent = state.exporting === 'preview' ? 'Gerando…' : 'Prévia';
  syncSubtitleStyle();
}

function setDirty(on) {
  state.dirty = on;
  $('saveTop').classList.toggle('dirty', on);
  if (on) setStatus('Alterações não salvas', 'dirty');
}

// ------------------------------------------------------------------ navegação e painéis

function setNav(collapsed, persist = true) {
  document.body.dataset.nav = collapsed ? 'collapsed' : 'expanded';
  $('navToggle').setAttribute('aria-expanded', String(!collapsed));
  $('navToggle').setAttribute('aria-label', collapsed ? 'Expandir menu' : 'Recolher menu');
  if (persist) store.set('studio.nav', collapsed ? 'collapsed' : 'expanded');
  syncScrim();
}
function syncScrim() {
  const sidebarOverlay = phone.matches && document.body.dataset.nav === 'expanded';
  const drawerOpen = narrow.matches && document.body.dataset.left === 'open';
  $('scrim').hidden = !(sidebarOverlay || drawerOpen);
}
function closeDrawers() {
  document.body.dataset.left = 'closed';
  if (phone.matches) setNav(true, false);
  syncScrim();
}
$('navToggle').onclick = () => setNav(document.body.dataset.nav !== 'collapsed');
$('scrim').onclick = closeDrawers;
$('leftClose').onclick = closeDrawers;
{
  const saved = store.get('studio.nav');
  setNav(phone.matches || (saved ? saved === 'collapsed' : window.innerWidth < 1500), false);
}

// Abas com navegação por setas (padrão WAI-ARIA); aria-controls aponta o painel de cada aba.
function initTabs(list, attr, onChange) {
  const tabs = [...list.querySelectorAll('[role=tab]')];
  const activate = (tab, focus = false) => {
    for (const t of tabs) {
      const on = t === tab;
      t.setAttribute('aria-selected', String(on));
      t.tabIndex = on ? 0 : -1;
      t.classList.toggle('active', on);
      $(t.getAttribute('aria-controls')).hidden = !on;
    }
    if (focus) tab.focus();
    onChange?.(tab.dataset[attr]);
  };
  list.addEventListener('click', (e) => { const t = e.target.closest('[role=tab]'); if (t) activate(t); });
  list.addEventListener('keydown', (e) => {
    const i = tabs.indexOf(document.activeElement);
    if (i < 0) return;
    let next = null;
    if (e.key === 'ArrowRight') next = tabs[(i + 1) % tabs.length];
    else if (e.key === 'ArrowLeft') next = tabs[(i - 1 + tabs.length) % tabs.length];
    else if (e.key === 'Home') next = tabs[0];
    else if (e.key === 'End') next = tabs[tabs.length - 1];
    if (next) { e.preventDefault(); activate(next, true); }
  });
  return (name) => activate(tabs.find((t) => t.dataset[attr] === name));
}

const NAV_FOR_LEFT = {media: 'media', cuts: 'cuts'};
const NAV_FOR_INSP = {props: 'editor', subtitles: 'subtitles', effects: 'effects', audio: 'audio'};
const setActiveNav = (name) => {
  for (const b of document.querySelectorAll('button.navitem')) {
    const on = b.dataset.nav === name;
    b.classList.toggle('active', on);
    if (on) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
  }
};
const showLeft = initTabs(document.querySelector('.leftpanel .tabs'), 'left', (name) => setActiveNav(NAV_FOR_LEFT[name]));
const showInsp = initTabs(document.querySelector('.inspector .tabs'), 'insp', (name) => setActiveNav(NAV_FOR_INSP[name]));

function openLeft(tab) {
  showLeft(tab);
  if (narrow.matches) {
    document.body.dataset.left = 'open';
    syncScrim();
    $(tab === 'cuts' ? 'tabCuts' : 'tabMedia').focus();
  }
}

function openAutomation() {
  loadAutomation();
  $('automationDialog').showModal();
}
function focusExports() {
  const panel = $('exportPanel');
  panel.focus();
  panel.classList.add('flash');
  setTimeout(() => panel.classList.remove('flash'), 900);
}
const NAV_ACTIONS = {
  editor() { closeDrawers(); showInsp('props'); $('workspace').focus(); },
  media() { openLeft('media'); },
  cuts() { openLeft('cuts'); },
  subtitles() { showInsp('subtitles'); },
  audio() { showInsp('audio'); },
  effects() { showInsp('effects'); },
  exports: focusExports,
  auto() {
    openLeft('media');
    $('mode').value = 'automatic';
    toggleMode();
    $('mode').focus();
  },
  automation: openAutomation,
  rights() { $('rightsDialog').showModal(); },
};
for (const b of document.querySelectorAll('button.navitem')) {
  b.onclick = () => {
    NAV_ACTIONS[b.dataset.nav]?.();
    if (phone.matches && !['media', 'cuts'].includes(b.dataset.nav)) closeDrawers();
  };
}
$('help').onclick = () => $('helpDialog').showModal();
narrow.addEventListener('change', () => { if (!narrow.matches) document.body.dataset.left = 'closed'; syncScrim(); });
phone.addEventListener('change', syncScrim);

// ------------------------------------------------------------------ biblioteca e importação

async function loadProjects() {
  $('projects').setAttribute('aria-busy', 'true');
  try {
    const d = await api('/api/projects');
    // Em análise: a lista mostra a etapa e o andamento e se atualiza sozinha até terminar.
    clearTimeout(state.projectsTimer);
    if (d.projects.some((p) => p.analysis)) state.projectsTimer = setTimeout(loadProjects, 5000);
    $('projects').innerHTML = d.projects.map((p) => {
      const selected = p.id === state.project?.id;
      const a = p.analysis;
      const status = a ? (a.status === 'queued' ? 'Na fila' : (STAGE_NAMES[a.stage] || 'Analisando')) + ' · ' +
        Math.round((a.progress || 0) * 100) + '%' : (PROJECT_STATUS[p.status] || p.status);
      return '<div class="projectrowitem"><button class="projectitem' + (selected ? ' selected' : '') + '" data-id="' +
        escape(p.id) + '"' + (selected ? ' aria-current="true"' : '') + '><strong>' + escape(p.name) + '</strong><small>' +
        '<span class="dot" data-status="' + escape(p.status) + '"></span>' + escape(status) +
        ' · ' + (p.duration ? fmt(p.duration) + ' · ' : '') + p.clips + (p.clips === 1 ? ' corte' : ' cortes') +
        '</small></button><button class="iconbtn sm projdel" data-del="' + escape(p.id) + '" data-name="' +
        escape(p.name) + '" title="Excluir projeto" aria-label="Excluir o projeto ' + escape(p.name) +
        '"><svg class="ic"><use href="#i-trash"/></svg></button></div>';
    }).join('') || '<p class="empty">Nenhum projeto ainda. Importe um vídeo no painel de mídia.</p>';
  } catch (e) {
    $('projects').innerHTML = '<p class="empty error">Não foi possível carregar os projetos.</p>';
    toast(e.message, true);
  } finally {
    $('projects').removeAttribute('aria-busy');
  }
}
$('projects').onclick = (e) => {
  const del = e.target.closest('.projdel');
  if (del) return deleteProject(del.dataset.del, del.dataset.name);
  const item = e.target.closest('.projectitem');
  if (!item) return;
  openProject(item.dataset.id).catch((err) => { setLoading(null); toast(err.message, true); });
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
    editing_mode: $('mode').value, target_seconds: $('target').value, desired_count: 0,  // 0: todos os trechos válidos
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
      setLoading('Baixando vídeo…');
      const result = await api('/api/imports/link', 'POST', {url: $('videoUrl').value.trim(), options: p});
      toast('Importação adicionada à fila.');
      await waitImport(result.id);
    } else {
      setStatus('Enviando vídeo…', 'busy');
      const d = await uploadFile(f, p);
      await openProject(d.id);
    }
  } catch (err) {
    setLoading(null);
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
  throw Error('A importação continua em segundo plano. Consulte os projetos recentes em instantes.');
}

// ------------------------------------------------------------------ projeto

function setThumb(dataUrl) {
  for (const id of ['projectThumb', 'mediaThumb']) {
    const box = $(id);
    if (dataUrl) {
      const img = document.createElement('img');
      img.alt = '';
      img.src = dataUrl;
      box.replaceChildren(img);
    } else {
      box.innerHTML = '<svg class="ic"><use href="#i-film"/></svg>';
    }
  }
}

async function openProject(id) {
  if (!/^[a-f0-9]{32}$/.test(id || '')) return;
  const version = ++state.openVersion;
  setStatus('Carregando projeto…', 'busy');
  setLoading('Carregando projeto…');
  const p = await api('/api/projects/' + id);
  if (version !== state.openVersion) return;
  const changed = state.project?.id !== id;
  state.project = p;
  if (changed) {
    state.stripKey = state.rulerKey = null;
    state.previewEnd = null;
    state.selected = p.clips?.[0]?.id || null;
    state.markIn = 0;
    state.markOut = Math.min(30, mediaDuration());
    setThumb(null);
    setDirty(false);
    hideExport();
    readLabel();
    computeScale();
    video.src = '/api/projects/' + id + '/source';
    history.replaceState({}, '', '/?projeto=' + id);
  } else if (!p.clips.some((c) => c.id === state.selected)) {
    state.selected = p.clips[0]?.id || null;
  }
  $('noVideo').hidden = true;
  $('projectName').textContent = $('mediaName').textContent = $('crumb').textContent = p.name;
  document.title = p.name + ' — AutoCortes Studio';
  const m = p.media;
  $('projectMeta').textContent = fmt(mediaDuration()) + ' · ' + m.height + 'p';
  $('mediaMeta').textContent = [fmt(mediaDuration()), m.width + '×' + m.height, m.fps ? Math.round(m.fps) + ' fps' : null,
    m.size_bytes ? fmtSize(m.size_bytes) : null, p.editing_mode === 'manual' ? 'Edição manual' : 'Seleção automática']
    .filter(Boolean).join(' · ');
  $('mediaCard').hidden = false;
  render();
  loadProjects();
  closeDrawers();
  if (p.status === 'queued' || p.status === 'analyzing') {
    const a = analysisLabel(p.analysis, p.analysis_events || []);
    setStatus(a.pct === null ? 'Analisando vídeo…' : 'Analisando vídeo · ' + a.pct + '%', 'busy');
    setLoading(a.text, a.progress, a.steps);
    setTimeout(() => {
      if (state.openVersion === version) openProject(id).catch((e) => toast(e.message, true));
    }, 1700);
  } else if (p.status === 'ready') {
    setStatus('Projeto aberto', 'ok');
    setLoading(video.readyState >= 2 ? null : 'Carregando vídeo…');
  } else {
    setLoading(null);
    setStatus('Projeto indisponível', 'error');
    toast(p.error || 'O projeto não está pronto para edição.', true);
  }
}

// Volta o editor ao estado inicial (usado quando o projeto aberto é excluído).
function closeProject() {
  state.openVersion++;
  state.stripToken++;  // interrompe a leitura das miniaturas
  state.project = null;
  state.selected = null;
  state.stripKey = state.rulerKey = state.strip = null;
  state.previewEnd = null;
  setDirty(false);
  video.pause();
  video.removeAttribute('src');
  video.load();
  setThumb(null);
  setLoading(null);
  hideExport();
  $('noVideo').hidden = false;
  $('mediaCard').hidden = true;
  $('projectName').textContent = $('crumb').textContent = 'Nenhum projeto aberto';
  $('projectMeta').textContent = 'Importe um vídeo ou abra um projeto recente';
  document.title = 'AutoCortes Studio — Editor';
  $('count').textContent = '0';
  $('cutCards').replaceChildren();
  $('properties').hidden = true;
  $('noSelection').hidden = false;
  for (const id of ['ruler', 'frames', 'wave', 'clipSegments']) $(id).replaceChildren();
  $('timelineCanvas').style.width = '';
  $('selectionBand').hidden = true;
  $('currentTime').textContent = $('duration').textContent = fmt(0);
  $('seek').value = 0;
  setFill($('seek'));
  selectionLabel();
  history.replaceState({}, '', '/');
  updateControls();
}

async function deleteProject(id, name) {
  if (!id) return;
  const ok = await askConfirm({
    title: 'Excluir projeto', yes: 'Excluir projeto', danger: true,
    text: 'O projeto “' + name + '” será apagado deste computador: o vídeo importado, os cortes e as exportações ' +
      'guardadas nele. Cópias já salvas na pasta de saída não são apagadas. Esta ação não pode ser desfeita.',
  });
  if (!ok) return;
  const wasOpen = state.project?.id === id;
  // O vídeo em reprodução mantém o arquivo aberto e, no Windows, impediria a exclusão.
  if (wasOpen) closeProject();
  try {
    await api('/api/projects/' + id, 'DELETE');
    setStatus('Projeto excluído', 'ok');
    toast('Projeto “' + name + '” excluído.');
  } catch (e) {
    toast(e.message, true);
    if (wasOpen) openProject(id).catch(() => {});
  } finally {
    loadProjects();
  }
}
$('deleteProject').onclick = () => { if (state.project) deleteProject(state.project.id, state.project.name); };

async function refresh() {
  if (!state.project) return;
  const p = await api('/api/projects/' + state.project.id);
  state.project = p;
  if (!p.clips.some((c) => c.id === state.selected)) state.selected = p.clips[0]?.id || null;
  render();
  loadProjects();  // mantém a contagem de cortes dos projetos recentes atualizada
}

function clipBadges(c) {
  const out = [];
  out.push(c.selection_mode === 'manual' ? 'Manual' : 'Automático');
  if (c.score != null) out.push('Nota ' + Math.round(c.score));
  const exported = Object.keys(c.exports || {});
  if (exported.length) out.push('Exportado ' + exported.join(', '));
  return out;
}

function syncFields(c) {
  // Não sobrescreve o campo que o usuário está editando ou que ainda não foi salvo.
  if (state.syncedClip !== c.id) { state.edited = {}; state.syncedClip = c.id; }
  for (const [id, value] of [['clipTitle', c.title], ['start', fmtMs(c.start)], ['end', fmtMs(c.end)]]) {
    if (document.activeElement !== $(id) && !state.edited[id]) $(id).value = value;
  }
  $('clipDuration').value = fmtMs(c.end - c.start);
  $('clipInfo').textContent = c.reasons?.length ? c.reasons.slice(0, 2).join(' · ') : '';
}

function render() {
  const p = state.project;
  if (!p) return updateControls();
  const c = current();
  $('count').textContent = p.clips.length;
  $('cutCards').innerHTML = p.clips.map((x) =>
    '<button class="cutCard' + (x.id === state.selected ? ' selected' : '') + '" data-id="' + x.id + '"' +
    (x.id === state.selected ? ' aria-current="true"' : '') + '><strong>' + escape(x.title) + '</strong><small>' +
    fmt(x.start) + ' — ' + fmt(x.end) + ' · ' + fmt(x.end - x.start) + '</small><span class="badges">' +
    clipBadges(x).map((b) => '<em' + (b.startsWith('Exportado') ? ' class="done"' : '') + '>' + escape(b) + '</em>').join('') +
    '</span></button>').join('') ||
    '<p class="empty">Nenhum corte ainda. Marque a entrada (I) e a saída (O) e use Cortar.</p>';
  $('properties').hidden = !c;
  $('noSelection').hidden = !!c;
  if (c && !state.drag) syncFields(c);
  selectionLabel();
  renderRights(p);
  renderPlans();
  renderMix();
  renderTimeline();
  updateControls();
}

// ------------------------------------------------------------------ enquadramento por trecho

const FRAMING_LABELS = {track: 'Seguir rosto', blur: 'Fundo desfocado', fit: 'Quadro inteiro',
  streamer: 'Só o streamer'};
function mixSegments(c) {
  return c?.framing_segments?.length ? c.framing_segments.map((s) => ({...s}))
    : (c ? [{start: 0, end: c.end - c.start, framing: 'blur'}] : []);
}
function renderMix() {
  const c = current();
  $('mixPanel').hidden = $('framing').value !== 'mix' || !c;
  if ($('mixPanel').hidden) return;
  const at = video.currentTime - c.start;
  $('mixSegments').innerHTML = mixSegments(c).map((s, i) =>
    '<li' + (at >= s.start && at < s.end ? ' class="here"' : '') + '>' +
    '<button type="button" class="t" data-seek="' + s.start + '" title="Ir para o início do trecho">' +
    fmt(s.start) + '–' + fmt(s.end) + '</button>' +
    '<select data-seg="' + i + '" aria-label="Enquadramento do trecho ' + (i + 1) + '">' +
    Object.entries(FRAMING_LABELS).map(([v, l]) => '<option value="' + v + '"' + (v === s.framing ? ' selected' : '') +
      '>' + l + '</option>').join('') + '</select>' +
    '<button type="button" class="iconbtn sm" data-del="' + i + '" aria-label="Remover trecho"' +
    (i === 0 && mixSegments(c).length === 1 ? ' disabled' : '') + '><svg class="ic"><use href="#i-x"/></svg></button></li>').join('');
}
async function saveMix(segments, {auto = false} = {}) {
  const c = current();
  if (!c) return;
  const base = '/api/projects/' + state.project.id + '/clips/' + c.id + '/framing';
  try {
    await (auto ? api(base + '/auto', 'POST') : api(base, 'PUT', {segments}));
    await refresh();
  } catch (e) { toast(e.message, true); }
}
$('framing').addEventListener('change', renderMix);
video.addEventListener('pause', renderMix);
$('mixSegments').onclick = (e) => {
  const c = current();
  const seek = e.target.closest('[data-seek]');
  if (seek && c) { video.currentTime = c.start + Number(seek.dataset.seek); renderMix(); return; }
  const del = e.target.closest('[data-del]');
  if (del) saveMix(mixSegments(c).filter((_, i) => i !== Number(del.dataset.del)));
};
$('mixSegments').onchange = (e) => {
  const sel = e.target.closest('[data-seg]');
  if (!sel) return;
  const segs = mixSegments(current());
  segs[Number(sel.dataset.seg)].framing = sel.value;
  saveMix(segs);
};
$('mixSplit').onclick = () => {
  const c = current();
  if (!c) return;
  const t = video.currentTime - c.start;
  const segs = mixSegments(c);
  const k = segs.findIndex((s) => t >= s.start && t < s.end);
  if (k < 0 || t - segs[k].start < 0.5 || segs[k].end - t < 0.5) {
    return toast('Pare o vídeo dentro do corte, longe do começo e do fim de um trecho.', true);
  }
  // Do ponto atual até o fim do trecho em que ele está.
  segs.splice(k, 1, {...segs[k], end: t}, {start: t, end: segs[k].end, framing: $('mixFrom').value});
  saveMix(segs);
};
$('mixAuto').onclick = () => saveMix(null, {auto: true});
$('mixClear').onclick = () => saveMix([]);

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

function readFields(c) {
  const title = $('clipTitle').value.trim();
  const start = state.edited.start ? parseTime($('start').value) : c.start;
  const end = state.edited.end ? parseTime($('end').value) : c.end;
  return {title, start, end};
}

async function save({silent = false} = {}) {
  const c = current();
  if (!c || !isReady()) return false;
  try {
    const {title, start, end} = readFields(c);
    $('start').removeAttribute('aria-invalid');
    $('end').removeAttribute('aria-invalid');
    $('clipTitle').removeAttribute('aria-invalid');
    if (!title) { $('clipTitle').setAttribute('aria-invalid', 'true'); throw Error('Dê um nome ao corte.'); }
    if (!Number.isFinite(start)) { $('start').setAttribute('aria-invalid', 'true'); throw Error('Início inválido. Use o formato mm:ss.mmm, por exemplo 01:15.300.'); }
    if (!Number.isFinite(end)) { $('end').setAttribute('aria-invalid', 'true'); throw Error('Fim inválido. Use o formato mm:ss.mmm, por exemplo 01:45.000.'); }
    if (start < 0 || end - start < 1 || end > mediaDuration() + .02) {
      $('end').setAttribute('aria-invalid', 'true');
      throw Error('Confira os tempos: o corte precisa ter pelo menos 1 segundo e terminar antes de ' + fmt(mediaDuration()) + '.');
    }
    if (title === c.title && start === c.start && end === c.end) {
      state.edited = {};
      setDirty(false);
      if (!silent) toast('Nenhuma alteração pendente.');
      return true;
    }
    const result = await api('/api/projects/' + state.project.id + '/clips/' + c.id, 'PATCH', {title, start, end});
    Object.assign(c, result);
    state.edited = {};
    await refresh();
    setDirty(false);
    setStatus('Salvo às ' + new Date().toLocaleTimeString('pt-BR', {hour: '2-digit', minute: '2-digit'}), 'ok');
    if (!silent) toast('Alterações salvas.');
    return true;
  } catch (e) {
    toast(e.message, true);
    if (!document.querySelector('[aria-invalid=true]')) await refresh().catch(() => {});
    return false;
  }
}

async function remove() {
  const c = current();
  if (!c) return;
  const ok = await askConfirm({
    title: 'Excluir corte', yes: 'Excluir', danger: true,
    text: 'O corte “' + c.title + '” será removido do projeto. O vídeo original não é alterado.',
  });
  if (!ok) return;
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
$('remove').onclick = $('removeSide').onclick = remove;
$('properties').onsubmit = (e) => { e.preventDefault(); save(); };
$('saveTop').onclick = () => save();
$('cutCards').onclick = (e) => {
  const card = e.target.closest('.cutCard');
  if (!card) return;
  select(Number(card.dataset.id));
  if (narrow.matches) closeDrawers();
};

// Campos de início/fim/nome: marcam alterações pendentes e atualizam a duração.
for (const id of ['clipTitle', 'start', 'end']) {
  $(id).addEventListener('input', () => {
    const c = current();
    if (!c) return;
    state.edited[id] = true;
    $(id).removeAttribute('aria-invalid');
    if (id !== 'clipTitle') {
      const s = state.edited.start ? parseTime($('start').value) : c.start;
      const e = state.edited.end ? parseTime($('end').value) : c.end;
      $('clipDuration').value = Number.isFinite(s) && Number.isFinite(e) && e > s ? fmtMs(e - s) : '—';
    }
    setDirty(true);
  });
}
for (const id of ['start', 'end']) {
  // Ao sair do campo, normaliza o texto para mm:ss.mmm quando o valor é válido.
  $(id).addEventListener('blur', () => {
    const t = parseTime($(id).value);
    if (state.edited[id] && Number.isFinite(t)) $(id).value = fmtMs(t);
  });
}

// ------------------------------------------------------------------ linha do tempo

function renderTimeline() {
  const p = state.project;
  if (!p) return;
  const duration = Math.max(1, mediaDuration());
  const rulerKey = p.id + ':' + duration + ':' + state.scale + ':' + LABEL;
  if (state.rulerKey !== rulerKey) {
    state.rulerKey = rulerKey;
    $('timelineCanvas').style.width = Math.max(640, duration * state.scale + LABEL + 8) + 'px';
    const minGap = 90 / state.scale;  // mantém os rótulos a ~90 px de distância
    const steps = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200];
    const step = steps.find((s) => s >= minGap) || 14400;
    let minor = step / (step % 5 === 0 ? 5 : step % 3 === 0 ? 3 : 2);
    if (duration / minor > 4000) minor = step;   // zoom máximo numa live longa: só as marcas principais
    const long = duration >= 3600;
    const ticks = [];
    for (let t = 0; t <= duration; t += minor) {
      const major = Math.abs(t / step - Math.round(t / step)) < 1e-6;
      ticks.push(el('span', major ? 'tick' : 'tick minor', {left: t * state.scale}, major ? fmtTick(t, long) : ''));
    }
    $('ruler').replaceChildren(...ticks);
  }
  $('clipSegments').replaceChildren(...p.clips.map((c) => {
    const width = Math.max(10, (c.end - c.start) * state.scale);
    const size = width < 34 ? ' tiny' : width < 90 ? ' narrow' : '';
    const b = el('button', 'segment' + size + (c.id === state.selected ? ' selected' : ''),
      {left: c.start * state.scale, width});
    b.type = 'button';
    b.dataset.id = c.id;
    // Cor pela nota (verde = melhores); cortes manuais/sem nota ficam neutros.
    if (c.score != null) b.style.setProperty('--hue', String(Math.round(clamp((c.score - 60) * 3.5, 0, 140))));
    b.title = (c.rank ? '#' + c.rank + ' ' : '') + c.title + '\n' + fmt(c.start) + ' — ' + fmt(c.end) +
      ' (' + fmt(c.end - c.start) + ')' + (c.score != null ? ' · nota ' + Math.round(c.score) : '') +
      '\nDois cliques: ampliar até o corte';
    if (c.id === state.selected) b.setAttribute('aria-current', 'true');
    const left = el('i', 'handle left'), right = el('i', 'handle right');
    left.dataset.side = 'start';
    right.dataset.side = 'end';
    left.setAttribute('aria-hidden', 'true');
    right.setAttribute('aria-hidden', 'true');
    b.append(left, el('em', 'num', null, String(c.rank || c.id)), el('b', '', null, c.title),
      el('small', '', null, fmt(c.end - c.start)), right);
    return b;
  }));
  updatePlayhead();
  buildFilmstrip();
  drawWave();
}

// Quadros reais do vídeo, lidos por um segundo elemento <video> oculto. Se o navegador
// não decodificar o arquivo, a faixa fica neutra (blocos lisos) em vez de simular imagens.
function layoutFrames() {
  for (const f of state.strip?.frames || []) {
    f.node.style.left = f.t0 * state.scale + 'px';
    f.node.style.width = Math.max(1, (f.t1 - f.t0) * state.scale) + 'px';
  }
}

function buildFilmstrip() {
  const p = state.project;
  const key = p.id + ':' + mediaDuration();
  if (state.stripKey === key) return layoutFrames();
  state.stripKey = key;
  const token = ++state.stripToken;
  const duration = mediaDuration();
  const n = clamp(Math.ceil(duration / 15), 8, 60);
  const gap = duration / n;
  const frames = [];
  for (let i = 0; i < n; i++) {
    const node = document.createElement('canvas');
    node.className = 'frame loading';
    node.width = 160;
    node.height = 90;
    frames.push({node, t0: i * gap, t1: (i + 1) * gap});
  }
  state.strip = {frames};
  $('frames').replaceChildren(...frames.map((f) => f.node));
  layoutFrames();
  // Tira gerada pelo servidor (FFmpeg, em cache); se falhar, o navegador lê os quadros como antes.
  const sprite = new Image();
  sprite.onload = () => {
    if (token !== state.stripToken) return;
    frames.forEach((f, i) => {
      f.node.getContext('2d').drawImage(sprite, i * 160, 0, 160, 90, 0, 0, 160, 90);
      f.node.classList.remove('loading');
    });
    if (!$('projectThumb').querySelector('img')) {
      try { setThumb(frames[Math.min(2, n - 1)].node.toDataURL('image/jpeg', 0.72)); } catch { /* sem miniatura */ }
    }
  };
  sprite.onerror = () => { if (token === state.stripToken) fillFilmstrip(token, p.id, frames); };
  sprite.src = '/api/projects/' + p.id + '/filmstrip?n=' + n;
  loadWave(token, p.id);
}

// ------------------------------------------------------------------ forma de onda

// Picos do áudio (0–255) gerados pelo servidor; desenhados só na parte visível da linha do tempo, então
// funcionam em qualquer zoom, mesmo numa live de horas.
async function loadWave(token, pid) {
  state.wave = null;
  setWaveInfo('Gerando a forma de onda…');
  for (let i = 0; i < 200 && token === state.stripToken; i++) {
    let d;
    try { d = await api('/api/projects/' + pid + '/waveform'); } catch { break; }
    if (token !== state.stripToken) return;
    if (d.state === 'ready') {
      const raw = atob(d.peaks);
      const peaks = new Uint8Array(raw.length);
      for (let k = 0; k < raw.length; k++) peaks[k] = raw.charCodeAt(k);
      state.wave = {bucket: d.bucket, peaks};
      setWaveInfo(null);
      return drawWave();
    }
    if (d.state !== 'pending') break;
    await sleep(4000);
  }
  if (token === state.stripToken) setWaveInfo('Forma de onda indisponível para este vídeo');
}
function setWaveInfo(text) {
  let info = $('wave').querySelector('.track-info');
  if (!text) { info?.remove(); return; }
  if (!info) { info = el('span', 'track-info'); $('wave').append(info); }
  info.textContent = text;
}
let waveFrame = 0;
function drawWave() {
  cancelAnimationFrame(waveFrame);
  waveFrame = requestAnimationFrame(() => {
    const lane = $('wave'), sc = $('timelineScroll'), w = state.wave;
    let canvas = lane.querySelector('canvas');
    if (!w) { canvas?.remove(); return; }
    if (!canvas) { canvas = document.createElement('canvas'); canvas.className = 'wavecanvas'; lane.prepend(canvas); }
    const width = Math.max(1, Math.min(sc.clientWidth - LABEL, lane.clientWidth));
    const height = lane.clientHeight || 48;
    const dpr = window.devicePixelRatio || 1;
    const left = Math.max(0, Math.min(sc.scrollLeft, lane.clientWidth - width));
    canvas.style.left = left + 'px';
    canvas.style.width = width + 'px';
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    ctx.fillStyle = getComputedStyle(lane).getPropertyValue('--wave') || '#4f7bd6';
    const mid = height / 2;
    for (let x = 0; x < width; x++) {
      // Maior pico entre as posições cobertas por este pixel.
      const a = Math.floor((left + x) / state.scale / w.bucket), b = Math.ceil((left + x + 1) / state.scale / w.bucket);
      let peak = 0;
      for (let k = a; k < Math.max(a + 1, b) && k < w.peaks.length; k++) peak = Math.max(peak, w.peaks[k]);
      const h = Math.max(1, (peak / 255) ** 1.6 * (height - 6));   // realça a fala sobre o ruído de fundo
      ctx.fillRect(x, mid - h / 2, 1, h);
    }
  });
}
$('timelineScroll').addEventListener('scroll', drawWave, {passive: true});
// Painel redimensionado (gaveta, janela, tela cheia): a onda acompanha a largura visível.
if (window.ResizeObserver) new ResizeObserver(drawWave).observe($('timelineScroll'));

async function fillFilmstrip(token, pid, frames) {
  const probe = document.createElement('video');
  probe.muted = true;
  probe.preload = 'auto';
  probe.playsInline = true;
  const cancelled = () => token !== state.stripToken;
  const seekTo = (t) => new Promise((resolve) => {
    const done = (ok) => { probe.removeEventListener('seeked', onSeek); clearTimeout(timer); resolve(ok); };
    const onSeek = () => done(true);
    const timer = setTimeout(() => done(false), 6000);
    probe.addEventListener('seeked', onSeek);
    probe.currentTime = t;
  });
  try {
    const ready = new Promise((resolve, reject) => {
      probe.onloadeddata = resolve;
      probe.onerror = () => reject(Error('sem decodificação'));
      setTimeout(() => reject(Error('tempo esgotado')), 15000);
    });
    probe.src = '/api/projects/' + pid + '/source';
    await ready;
    for (let i = 0; i < frames.length; i++) {
      if (cancelled()) return;
      const f = frames[i];
      if (!await seekTo((f.t0 + f.t1) / 2)) continue;
      const ctx = f.node.getContext('2d');
      ctx.drawImage(probe, 0, 0, f.node.width, f.node.height);
      if (i === Math.min(2, frames.length - 1) && !$('projectThumb').querySelector('img')) {
        try { setThumb(f.node.toDataURL('image/jpeg', 0.72)); } catch { /* quadro não exportável */ }
      }
      await sleep(25);
    }
  } catch { /* faixa permanece neutra */ } finally {
    probe.removeAttribute('src');
    probe.load();
  }
}

function updatePlayhead() {
  const t = video.currentTime || 0;
  const x = LABEL + t * state.scale;
  $('playhead').style.left = x + 'px';
  $('currentTime').textContent = fmt(t);
  $('duration').textContent = fmt(mediaDuration());
  if (!state.seeking) {
    $('seek').value = mediaDuration() ? 1000 * t / mediaDuration() : 0;
    setFill($('seek'));
  }
  // Acompanha o cursor durante a reprodução.
  const sc = $('timelineScroll');
  if (!video.paused && !state.scrubbing && (x > sc.scrollLeft + sc.clientWidth - 32 || x < sc.scrollLeft + LABEL)) {
    sc.scrollLeft = Math.max(0, x - LABEL - 48);
  }
}

// Zoom 1 = vídeo inteiro cabe na largura visível; cada nível amplia a partir daí.
const ZOOM_STEP = 1.6;
const ZOOM_MAX_PX = 60;    // zoom máximo: ~60 px por segundo, em qualquer duração de vídeo
function computeScale() {
  const visible = Math.max(200, $('timelineScroll').clientWidth - LABEL - 8);
  const fit = mediaDuration() ? visible / mediaDuration() : 7;
  // Quantos níveis cabem entre "vídeo inteiro" e 60 px/s: numa live de horas são bem mais que 8.
  const levels = clamp(Math.ceil(Math.log(ZOOM_MAX_PX / fit) / Math.log(ZOOM_STEP)) + 1, 8, 40);
  $('zoom').max = levels;
  state.scale = Math.min(200, fit * Math.pow(ZOOM_STEP, Number($('zoom').value) - 1));
}

// ``anchor``: tempo que deve ficar parado na tela (sob o cursor) durante o zoom.
function setZoom(value, anchor = null) {
  const sc = $('timelineScroll');
  const before = anchor ? anchor.t * state.scale - sc.scrollLeft : 0;
  computeScale();
  $('zoom').value = clamp(value, 1, Number($('zoom').max));
  setFill($('zoom'));
  computeScale();
  renderTimeline();
  selectionLabel();
  if (anchor) sc.scrollLeft = Math.max(0, anchor.t * state.scale - before);
}

// Amplia até o corte ocupar ~70% da largura visível e rola até ele.
function zoomToClip(c) {
  const sc = $('timelineScroll');
  const visible = Math.max(200, sc.clientWidth - LABEL - 8);
  const fit = visible / mediaDuration();
  const want = visible * 0.7 / Math.max(1, c.end - c.start);
  const level = Math.floor(Math.log(want / fit) / Math.log(ZOOM_STEP)) + 1;
  setZoom(level);
  sc.scrollLeft = Math.max(0, (c.start + c.end) / 2 * state.scale - visible / 2);
}

$('timelineScroll').addEventListener('wheel', (e) => {
  if (!(e.ctrlKey || e.metaKey) || !state.project) return;   // rolagem normal continua rolando
  e.preventDefault();
  const sc = $('timelineScroll');
  const x = e.clientX - sc.getBoundingClientRect().left - LABEL + sc.scrollLeft;
  setZoom(Number($('zoom').value) + (e.deltaY < 0 ? 1 : -1), {t: Math.max(0, x / state.scale)});
}, {passive: false});
$('clipSegments').addEventListener('dblclick', (e) => {
  const btn = e.target.closest('.segment');
  const c = btn && state.project?.clips.find((x) => x.id === Number(btn.dataset.id));
  if (c) zoomToClip(c);
});
let resizeTimer;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { readLabel(); if (state.project) setZoom(Number($('zoom').value)); }, 150);
});
$('zoom').oninput = () => setZoom(Number($('zoom').value));
$('zoomIn').onclick = () => setZoom(Number($('zoom').value) + 1);
$('zoomOut').onclick = () => setZoom(Number($('zoom').value) - 1);
$('fit').onclick = () => { setZoom(1); $('timelineScroll').scrollLeft = 0; };

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
  $('start').value = fmtMs(c.start);
  $('end').value = fmtMs(c.end);
  $('clipDuration').value = fmtMs(c.end - c.start);
  const btn = $('clipSegments').querySelector('[data-id="' + c.id + '"]');
  if (btn) {
    btn.style.left = c.start * state.scale + 'px';
    btn.style.width = (c.end - c.start) * state.scale + 'px';
    btn.querySelector('small').textContent = fmt(c.end - c.start);
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
    state.edited = {};
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

// Clicar ou arrastar na régua e nas faixas move o cursor.
function seekFromTrack(e) {
  if (!state.project) return;
  const b = e.currentTarget.getBoundingClientRect();
  video.currentTime = clamp((e.clientX - b.left) / state.scale, 0, mediaDuration());
}
for (const lane of [$('ruler'), $('frames'), $('wave')]) {
  lane.onpointerdown = (e) => {
    if (e.button || !state.project) return;
    state.scrubbing = true;
    lane.setPointerCapture(e.pointerId);
    seekFromTrack(e);
    lane.onpointermove = seekFromTrack;
  };
  const end = () => { state.scrubbing = false; lane.onpointermove = null; };
  lane.onpointerup = lane.onpointercancel = end;
}

// ------------------------------------------------------------------ player

video.ontimeupdate = () => {
  updatePlayhead();
  if (state.previewEnd !== null && video.currentTime >= state.previewEnd) {
    video.pause();
    state.previewEnd = null;
  }
};
video.onplay = () => { $('play').dataset.state = 'playing'; $('play').setAttribute('aria-label', 'Pausar'); };
video.onpause = () => { $('play').dataset.state = 'paused'; $('play').setAttribute('aria-label', 'Reproduzir'); };
video.onloadeddata = () => { if (isReady()) setLoading(null); };
video.onwaiting = () => { if (state.project && !video.paused) setLoading('Carregando…'); };
video.onplaying = () => setLoading(null);
video.onerror = () => {
  if (!state.project) return;
  setLoading(null);
  setStatus('Vídeo indisponível', 'error');
  toast('O navegador não conseguiu reproduzir este vídeo. A edição e a exportação continuam funcionando; ' +
    'para pré-visualizar, converta o arquivo para MP4 (H.264).', true);
};
$('seek').onpointerdown = () => { state.seeking = true; };
$('seek').onpointerup = () => { state.seeking = false; };
$('seek').oninput = () => {
  setFill($('seek'));
  if (state.project) video.currentTime = Number($('seek').value) / 1000 * mediaDuration();
};
const nudge = (s) => { video.currentTime = clamp(video.currentTime + s, 0, mediaDuration()); };
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
$('mute').onclick = () => {
  video.muted = !video.muted;
  $('mute').setAttribute('aria-pressed', String(video.muted));
  $('mute').setAttribute('aria-label', video.muted ? 'Ativar som' : 'Silenciar');
};
$('fullscreen').onclick = () => {
  if (document.fullscreenElement) document.exitFullscreen();
  else $('videoBox').requestFullscreen?.().catch(() => toast('Tela cheia indisponível neste navegador.', true));
};

document.addEventListener('keydown', (e) => {
  if (anyDialogOpen()) return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
    e.preventDefault();
    if (current()) save();
    return;
  }
  if (e.target.closest('input, textarea, select, [contenteditable]') || e.altKey || e.ctrlKey || e.metaKey) return;
  if (e.key === 'Escape') { $('toast').hidden = true; closeDrawers(); return; }
  if (e.key === '?') { e.preventDefault(); $('help').click(); return; }
  if (!state.project) return;
  const k = e.key.toLowerCase();
  // Espaço sobre um botão focado continua acionando o botão.
  if (e.code === 'Space' && !e.target.closest('button, a, [role=tab]')) { e.preventDefault(); $('play').click(); }
  else if (k === 'i') $('markIn').click();
  else if (k === 'o') $('markOut').click();
  else if (k === 'n') create();
  else if (k === 's' && current()) split();
  else if (k === 'm') $('mute').click();
  else if (k === 'f') $('fullscreen').click();
  else if (e.key === '+' || e.key === '=') $('zoomIn').click();
  else if (e.key === '-') $('zoomOut').click();
  else if ((e.key === 'Delete' || e.key === 'Backspace') && current()) { e.preventDefault(); remove(); }
  else if (e.key === 'ArrowLeft' && !e.target.closest('input[type=range], [role=tab]')) { e.preventDefault(); nudge(e.shiftKey ? -5 : -1); }
  else if (e.key === 'ArrowRight' && !e.target.closest('input[type=range], [role=tab]')) { e.preventDefault(); nudge(e.shiftKey ? 5 : 1); }
});

for (const b of document.querySelectorAll('[data-format]')) {
  b.onclick = () => {
    state.format = b.dataset.format;
    $('formatChip').textContent = state.format === 'original' ? 'Orig.' : state.format;
    for (const item of document.querySelectorAll('[data-format]')) {
      item.classList.toggle('active', item === b);
      item.setAttribute('aria-pressed', String(item === b));
    }
  };
}

// ------------------------------------------------------------------ estilo de edição (plano)

const PLAN_LABELS = {dinamico: 'Dinâmica', equilibrado: 'Equilibrada', cinematografico: 'Cinematográfica'};
const planStale = (c) => {
  const m = c?.edit_plans_meta;
  return !!m && (Math.abs(m.start - c.start) > 0.01 || Math.abs(m.end - c.end) > 0.01);
};
function planSummary(plan) {
  const on = plan.actions.filter((a) => a.enabled !== false);
  const removed = on.filter((a) => a.type === 'cut_pause').reduce((t, a) => t + a.end - a.start, 0);
  const zooms = on.filter((a) => a.type === 'zoom' || a.type === 'push').length;
  const highlights = on.filter((a) => a.type === 'highlight').length;
  return plan.style_label + ' · ' + zooms + ' zoom(s) · ' + highlights + ' destaque(s) · ' +
    removed.toFixed(1) + ' s de pausas removidos · duração final ' + fmt(plan.analysis.duration - removed);
}
function selectedPlanName() {
  const c = current();
  const plans = c?.edit_plans || {};
  return c?.edit_plan_selected in plans ? c.edit_plan_selected : Object.keys(plans)[0];
}
function renderPlans() {
  const c = current();
  const plans = c?.edit_plans || {};
  const names = Object.keys(plans);
  $('planResult').hidden = !names.length;
  syncSubtitleStyle();
  if (!names.length) return;
  const name = selectedPlanName(), plan = plans[name];
  $('planVersions').innerHTML = names.length > 1 ? names.map((n) =>
    '<button type="button" data-plan="' + escape(n) + '" aria-pressed="' + (n === name) + '">' +
    escape(PLAN_LABELS[n] || plans[n].style_label) + '</button>').join('') : '';
  const a = plan.analysis || {};
  $('planReason').textContent = plan.reason + (a.content_type ? ' Conteúdo: ' + a.content_type +
    ' (confiança ' + a.confidence + ')' + (a.evidence?.length ? ' — ' + a.evidence.join(', ') : '') + '.' : '');
  $('planSummary').textContent = planSummary(plan);
  $('planLimits').hidden = !a.limitations?.length;
  $('planLimits').textContent = (a.limitations || []).join(' ');
  $('planActions').innerHTML = plan.actions.map((act) =>
    '<li><label><input type="checkbox" data-act="' + act.id + '"' + (act.enabled !== false ? ' checked' : '') + '>' +
    '<span class="t">' + fmt(act.start) + '</span><b>' + escape(act.label) + '</b><small>' + escape(act.reason) +
    '</small></label></li>').join('') || '<li class="hint">Nenhuma ação: o trecho não pede efeitos neste estilo.</li>';
  $('planStale').hidden = !planStale(c);
}

async function planEdit(variants) {
  const c = current();
  if (!c || !isReady()) return;
  const btn = $(variants ? 'variantsBtn' : 'planBtn');
  const options = {};
  for (const box of document.querySelectorAll('[data-opt]')) options[box.dataset.opt] = box.checked;
  btn.setAttribute('aria-busy', 'true');
  btn.disabled = true;
  setStatus('Analisando o trecho…', 'busy');
  try {
    if (state.dirty && !await save({silent: true})) return;
    await api('/api/projects/' + state.project.id + '/clips/' + c.id + '/plans', 'POST', {
      style: $('editStyle').value, intensity: Number($('editIntensity').value) / 100, variants, options,
    });
    $('usePlan').checked = true;
    await refresh();
    setStatus('Plano de edição pronto', 'ok');
    toast(variants ? 'Três versões planejadas. Compare e escolha uma.' : 'Plano de edição pronto.');
  } catch (e) {
    setStatus('Falha ao planejar', 'error');
    toast(e.message, true);
  } finally {
    btn.removeAttribute('aria-busy');
    btn.disabled = false;
  }
}
async function patchPlan(body) {
  const c = current();
  try {
    await api('/api/projects/' + state.project.id + '/clips/' + c.id + '/plans/' + selectedPlanName(), 'PATCH', body);
    await refresh();
  } catch (e) { toast(e.message, true); }
}
$('planBtn').onclick = () => planEdit(false);
$('variantsBtn').onclick = () => planEdit(true);
$('editIntensity').oninput = () => {
  $('intensityOut').textContent = $('editIntensity').value + '%';
  setFill($('editIntensity'));
};
$('planVersions').onclick = async (e) => {
  const b = e.target.closest('[data-plan]');
  if (!b) return;
  const c = current();
  try {
    await api('/api/projects/' + state.project.id + '/clips/' + c.id + '/plans/' + b.dataset.plan, 'PATCH',
      {selected: true});
    await refresh();
  } catch (err) { toast(err.message, true); }
};
$('planActions').onchange = (e) => {
  const box = e.target.closest('[data-act]');
  if (box) patchPlan({actions: {[box.dataset.act]: box.checked}});
};
function exportPlanName() {
  const c = current();
  return $('usePlan').checked && c?.edit_plans && !planStale(c) ? selectedPlanName() || null : null;
}
$('usePlan').addEventListener('change', syncSubtitleStyle);

// ------------------------------------------------------------------ exportação

function hideExport() {
  $('exportPanel').dataset.tone = '';
  $('downloads').replaceChildren();
  $('jobStatus').textContent = EXPORT_IDLE;
  $('exportBar').hidden = true;
  $('cancelExport').hidden = true;
}

function showExport(text, {progress = null, cancellable = false, tone = ''} = {}) {
  $('exportPanel').dataset.tone = tone;
  $('jobStatus').textContent = text;
  $('exportBar').hidden = progress === null;
  if (progress !== null) $('exportBar').value = Math.round(progress * 100);
  $('cancelExport').hidden = !cancellable;
}

function exportOptions(format, rights) {
  return {
    format, framing: $('framing').value, subtitles: $('subtitles').checked,
    subtitle_style: $('subtitleStyle').value, refine_subtitles: $('refineSubtitles').checked,
    normalize_audio: $('normalize').checked, hook: $('hook').checked, acknowledge_rights: rights,
    edit_plan: exportPlanName(),
  };
}

// Estilo da legenda: lembrado entre sessões; com um plano de edição em uso, o plano decide.
$('subtitleStyle').value = store.get('subtitleStyle') === 'classica' ? 'classica' : 'dinamica';
$('subtitleStyle').addEventListener('change', () => store.set('subtitleStyle', $('subtitleStyle').value));
$('refineSubtitles').checked = store.get('refineSubtitles') !== 'off';
$('refineSubtitles').addEventListener('change', () => store.set('refineSubtitles',
  $('refineSubtitles').checked ? 'on' : 'off'));
function syncSubtitleStyle() {
  const planned = Boolean(exportPlanName());
  $('subtitleStyle').disabled = !$('subtitles').checked || planned;
  $('subtitleStyleNote').hidden = !planned || !$('subtitles').checked;
  $('refineSubtitles').disabled = !$('subtitles').checked;
}
$('subtitles').addEventListener('change', syncSubtitleStyle);

// Prévia: a mesma montagem da exportação (layout, legenda, plano de edição) em meia resolução.
async function previewEdit() {
  const c = current();
  if (!c || state.exporting || !isReady()) return toast('Selecione um corte pronto.', true);
  const pid = state.project.id, cid = c.id, format = state.format;
  state.exporting = 'preview';
  updateControls();
  try {
    if (!await save({silent: true})) return;
    $('downloads').replaceChildren();
    showExport('Gerando a prévia…', {progress: 0});
    setStatus('Gerando prévia…', 'busy');
    const j = await api('/api/projects/' + pid + '/clips/' + cid + '/preview', 'POST', exportOptions(format, false));
    state.exportJob = j.id;
    await monitorJob(j.id, pid, cid, format, {preview: true});
  } catch (e) {
    if (state.project?.id === pid) showExport(e.message, {tone: 'error'});
    setStatus('Prévia não concluída', 'error');
    toast(e.message, true);
  } finally {
    state.exporting = false;
    state.exportJob = null;
    updateControls();
  }
}

function openPreview(src, record, {preview = true} = {}) {
  const v = $('previewVideo');
  const portrait = !record?.height || record.height >= record.width;
  v.classList.toggle('wide', !portrait);
  v.src = src;
  $('previewTitle').textContent = preview ? 'Prévia da edição' : 'Vídeo exportado';
  $('previewNote').textContent = preview
    ? 'Renderização rápida em meia resolução, com o mesmo recorte, legenda e efeitos da exportação final.'
    : 'Arquivo final, como foi exportado.';
  $('previewExport').hidden = !preview;
  const q = record?.quality || {};
  const facts = [];
  const dur = record?.validation?.duration;
  if (dur) facts.push(['Duração', fmt(dur), '']);
  if (record?.width) facts.push(['Resolução', record.width + '×' + record.height + (preview ? ' (prévia)' : ''), '']);
  const plan = record?.edit_plan;
  facts.push(['Plano de edição', plan ? plan.style + ' · ' + plan.actions.length + ' ação(ões)' : 'nenhum', '']);
  if (q.subtitles) {
    const r = q.subtitles.max_width_ratio;
    facts.push(['Legenda mais larga', Math.round(r * 100) + '% da área útil', r <= 0.95 ? 'good' : 'warn']);
  }
  if (q.reframe && q.reframe.face_visibility_p05 != null) {
    const v5 = q.reframe.face_visibility_p05;
    facts.push(['Rosto visível (95% do tempo)', Math.round(v5 * 100) + '%', v5 >= 0.95 ? 'good' : 'warn']);
    if (q.reframe.fit_seconds) facts.push(['Quadro inteiro (sem recorte seguro)', q.reframe.fit_seconds.toFixed(1) + ' s', '']);
  }
  $('previewFacts').innerHTML = facts.map(([k, val, tone]) =>
    '<li class="' + tone + '"><span>' + escape(k) + '</span><b>' + escape(val) + '</b></li>').join('');
  $('previewWarnings').innerHTML = (record?.warnings || []).map((w) => '<li>' + escape(w) + '</li>').join('');
  $('previewDialog').showModal();
  v.play().catch(() => {});
}
function closePreview() {
  const v = $('previewVideo');
  v.pause();
  v.removeAttribute('src');
  v.load();
}
$('previewDialog').addEventListener('close', closePreview);
$('previewClose').onclick = () => { $('previewDialog').close(); closePreview(); };
$('previewExport').onclick = () => { $('previewDialog').close(); closePreview(); exportClip(); };
$('previewTop').onclick = previewEdit;

async function exportClip() {
  const c = current();
  if (!c || state.exporting || !isReady()) return toast('Selecione um corte pronto para exportar.', true);
  const pid = state.project.id, cid = c.id, format = state.format;
  state.exporting = true;
  updateControls();
  try {
    if (!await save({silent: true})) return;
    const rights = !!state.project.rights?.requires_acknowledgement;
    if (rights && !await askConfirm({
      title: 'Alertas de direitos autorais', yes: 'Confirmo e quero exportar',
      text: 'Este material tem alertas de direitos autorais. Confirma que pode utilizá-lo?',
    })) return;
    $('downloads').replaceChildren();
    showExport('Enviando para a fila…', {progress: 0});
    setStatus('Exportando…', 'busy');
    const j = await api('/api/projects/' + pid + '/clips/' + cid + '/export', 'POST', exportOptions(format, rights));
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

async function monitorJob(id, pid, cid, format, {preview = false} = {}) {
  for (let i = 0; i < 2400; i++) {
    const j = await api('/api/jobs/' + id);
    const here = state.project?.id === pid;
    if (here && ACTIVE_JOB.includes(j.status)) {
      showExport((j.message || 'Processando') + ' · ' + Math.round((j.progress || 0) * 100) + '%',
        {progress: j.progress || 0, cancellable: j.status !== 'cancelling'});
    }
    if (j.status === 'done' && preview) {
      setStatus('Prévia pronta', 'ok');
      if (!here) return toast('Prévia pronta em outro projeto.');
      await refresh();
      showExport('Prévia pronta.', {tone: 'ok'});
      const src = '/api/projects/' + pid + '/clips/' + cid + '/preview?v=' + Date.now();
      const watch = document.createElement('button');
      watch.type = 'button';
      watch.className = 'btn block';
      watch.innerHTML = '<svg class="ic"><use href="#i-eye"/></svg>';
      watch.append('Assistir prévia');
      watch.onclick = () => openPreview(src, j.result);
      $('downloads').replaceChildren(watch);
      openPreview(src, j.result);
      return;
    }
    if (j.status === 'done') {
      setStatus('Exportação concluída', 'ok');
      if (!here) return toast('Exportação concluída em outro projeto.');
      await refresh();
      showExport('Exportação concluída.', {tone: 'ok'});
      const a = document.createElement('a');
      a.className = 'btn primary block';
      a.href = '/api/projects/' + pid + '/clips/' + cid + '/download?format=' + encodeURIComponent(format);
      a.innerHTML = '<svg class="ic"><use href="#i-download"/></svg>';
      a.append('Baixar MP4 (' + format + ')');
      const watch = document.createElement('button');
      watch.type = 'button';
      watch.className = 'btn block';
      watch.innerHTML = '<svg class="ic"><use href="#i-eye"/></svg>';
      watch.append('Assistir');
      watch.onclick = () => openPreview(a.href, j.result, {preview: false});
      $('downloads').replaceChildren(a, watch);
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
$('refresh').onclick = loadProjects;

// ------------------------------------------------------------------ início

readLabel();
setFill($('zoom'));
setFill($('seek'));
setFill($('editIntensity'));
hideExport();
loadProjects();
updateControls();
const initial = new URLSearchParams(location.search).get('projeto');
if (initial) openProject(initial).catch((e) => { setLoading(null); setStatus('Projeto não encontrado', 'error'); toast(e.message, true); });
