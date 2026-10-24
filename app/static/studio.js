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
// mm:ss.d para rótulos; mm:ss.mmm para os campos de início e fim.
const fmt = (n) => {
  n = Math.max(0, Number(n) || 0);
  return String(Math.floor(n / 60)).padStart(2, '0') + ':' + (n % 60).toFixed(1).padStart(4, '0');
};
const fmtMs = (n) => {
  n = Math.max(0, Math.round((Number(n) || 0) * 1000));
  const ms = n % 1000, s = Math.floor(n / 1000) % 60, m = Math.floor(n / 60000);
  return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0') + '.' + String(ms).padStart(3, '0');
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

function setLoading(text) {
  $('loading').hidden = !text;
  if (text) $('loadingText').textContent = text;
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
  $('exportLabel').textContent = state.exporting ? 'Exportando…' : 'Exportar';
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
    $('projects').innerHTML = d.projects.map((p) => {
      const selected = p.id === state.project?.id;
      return '<div class="projectrowitem"><button class="projectitem' + (selected ? ' selected' : '') + '" data-id="' +
        escape(p.id) + '"' + (selected ? ' aria-current="true"' : '') + '><strong>' + escape(p.name) + '</strong><small>' +
        '<span class="dot" data-status="' + escape(p.status) + '"></span>' + escape(PROJECT_STATUS[p.status] || p.status) +
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
    setStatus('Analisando vídeo…', 'busy');
    setLoading('Analisando vídeo…');
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
    const minGap = 80 / state.scale;  // mantém os rótulos a ~80 px de distância
    const step = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600].find((s) => s >= minGap) || 900;
    const ticks = [];
    for (let t = 0; t <= duration; t += step) ticks.push(el('span', 'tick', {left: t * state.scale}, fmt(t).replace(/\.0$/, '')));
    $('ruler').replaceChildren(...ticks);
  }
  $('clipSegments').replaceChildren(...p.clips.map((c) => {
    const b = el('button', 'segment' + (c.id === state.selected ? ' selected' : ''),
      {left: c.start * state.scale, width: Math.max(4, (c.end - c.start) * state.scale)});
    b.type = 'button';
    b.dataset.id = c.id;
    b.title = c.title + ' (' + fmt(c.start) + ' — ' + fmt(c.end) + ')';
    if (c.id === state.selected) b.setAttribute('aria-current', 'true');
    const left = el('i', 'handle left'), right = el('i', 'handle right');
    left.dataset.side = 'start';
    right.dataset.side = 'end';
    left.setAttribute('aria-hidden', 'true');
    right.setAttribute('aria-hidden', 'true');
    b.append(left, el('b', '', null, c.title), el('small', '', null, fmt(c.end - c.start)), right);
    return b;
  }));
  updatePlayhead();
  buildFilmstrip();
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
  const n = clamp(Math.ceil(duration / 15), 8, 40);
  const gap = duration / n;
  const frames = [];
  for (let i = 0; i < n; i++) {
    const node = document.createElement('canvas');
    node.className = 'frame';
    node.width = 128;
    node.height = 72;
    frames.push({node, t0: i * gap, t1: (i + 1) * gap});
  }
  state.strip = {frames};
  $('frames').replaceChildren(...frames.map((f) => f.node));
  $('wave').innerHTML = '<span class="track-info">A forma de onda ainda não está disponível nesta versão</span>';
  layoutFrames();
  fillFilmstrip(token, p.id, frames);
}

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
function computeScale() {
  const visible = Math.max(200, $('timelineScroll').clientWidth - LABEL - 8);
  const fit = mediaDuration() ? visible / mediaDuration() : 7;
  state.scale = Math.min(200, fit * Math.pow(1.6, Number($('zoom').value) - 1));
}

function setZoom(value) {
  $('zoom').value = clamp(value, 1, 8);
  setFill($('zoom'));
  computeScale();
  renderTimeline();
  selectionLabel();
}
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
      a.className = 'btn primary block';
      a.href = '/api/projects/' + pid + '/clips/' + cid + '/download?format=' + encodeURIComponent(format);
      a.innerHTML = '<svg class="ic"><use href="#i-download"/></svg>';
      a.append('Baixar MP4 (' + format + ')');
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
$('refresh').onclick = loadProjects;

// ------------------------------------------------------------------ início

readLabel();
setFill($('zoom'));
setFill($('seek'));
hideExport();
loadProjects();
updateControls();
const initial = new URLSearchParams(location.search).get('projeto');
if (initial) openProject(initial).catch((e) => { setLoading(null); setStatus('Projeto não encontrado', 'error'); toast(e.message, true); });
