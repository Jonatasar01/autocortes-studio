'use strict';
// AutoCortes Studio — editor manual. Usa a mesma API do editor clássico; o motor
// de edição (seleção, FFmpeg, legendas, direitos, tarefas) continua no servidor.

const $ = (id) => document.getElementById(id);
const state = {
  project: null, selected: null, format: '9:16', markIn: 0, markOut: 0, scale: 7,
  previewEnd: null, drag: null, stripKey: null, stripToken: 0, rulerKey: null, openVersion: 0,
  exporting: false, exportJob: null, edited: {}, syncedClip: null, dirty: false, seeking: false,
  queueTimer: null, queueClosed: {}, picked: new Set(), batch: null,
};
const CSRF = {'X-AutoCortes': '1'};
const ACTIVE_JOB = ['queued', 'running', 'cancelling'];
const FAILED_JOB = ['failed', 'cancelled', 'interrupted'];
const PROJECT_STATUS = {
  ready: 'Pronto', queued: 'Na fila', analyzing: 'Analisando', failed: 'Falhou',
  interrupted: 'Interrompido', cancelled: 'Cancelado', error: 'Com erro',
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
  cenas: 'Cenas', audio_eventos: 'Reações no áudio', selecionando: 'Seleção', ia_local: 'IA local', curadoria_ia: 'Curadoria IA', fila: 'Fila'};
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
  if (!r.ok) {
    const err = Error(withHint(typeof data.detail === 'string' ? data.detail : 'Erro ao executar a operação.', data.code));
    err.code = data.code;
    throw err;
  }
  return data;
}

// O que fazer quando algo dá errado: a mensagem do servidor diz o problema; aqui entra a saída.
const ERROR_HINTS = {
  too_large: 'Use um arquivo menor ou aumente MAX_UPLOAD_GB no iniciar_windows.bat.',
  too_long: 'Corte o vídeo em partes ou aumente AUTOCORTES_MAX_SOURCE_HOURS no iniciar_windows.bat.',
  bad_extension: 'Converta para MP4 (H.264) e tente de novo.',
  no_audio: 'Sem áudio não há fala para analisar: use o modo Manual.',
  busy: 'Espere a tarefa atual terminar ou cancele em Processamentos.',
  not_ready: 'Aguarde a análise terminar; o andamento aparece na página inicial.',
  no_source: 'O vídeo original sumiu da pasta do projeto: crie o projeto de novo com o arquivo.',
  no_transcript: 'Use "Analisar de novo" para transcrever o vídeo.',
  disk_full: 'Libere espaço no disco e tente de novo.',
  rights_ack_required: 'Abra a Revisão de direitos, leia os alertas e confirme na exportação.',
};
function withHint(message, code) {
  const hint = ERROR_HINTS[code];
  return hint && !message.includes(hint) ? message + ' ' + hint : message;
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
  $('exportLabel').textContent = state.exporting === true ? 'Enviando…' : 'Exportar';
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

// Página inicial ("home") e editor dividem a área principal; o resto abre por cima (diálogos e gavetas).
const setActiveNav = (name) => {
  for (const b of document.querySelectorAll('button.navitem')) {
    const on = b.dataset.nav === name;
    b.classList.toggle('active', on);
    if (on) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
  }
};
function setView(view) {
  document.body.dataset.view = view;
  $('homeView').hidden = view !== 'home';
  setActiveNav(view === 'home' ? (state.projectsAll ? 'projects' : 'home') : 'editor');
  if (view === 'home') {
    video.pause();
    $('crumb').textContent = 'Início';
    document.title = 'AutoCortes Studio';
    history.replaceState({}, '', '/');   // recarregar a página volta para o início
    loadProjects();
  } else if (state.project) {
    $('crumb').textContent = state.project.name;
    document.title = state.project.name + ' — AutoCortes Studio';
    history.replaceState({}, '', '/?projeto=' + state.project.id);
  }
}
const showLeft = initTabs(document.querySelector('.leftpanel .tabs'), 'left');
const showInsp = initTabs(document.querySelector('.inspector .tabs'), 'insp', (name) => {
  if (name === 'effects') loadFilterSamples();
});

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
  home() { state.projectsAll = false; setView('home'); },
  new() { openNewProject(); },
  projects() {
    state.projectsAll = true;
    setView('home');
    $('projectsSec').scrollIntoView({block: 'start'});
  },
  editor() {
    if (!state.project) {
      toast('Abra um projeto ou crie um novo para usar o editor.');
      return NAV_ACTIONS.projects();
    }
    setView('editor');
    closeDrawers();
    $('workspace').focus();
  },
  jobs: openJobs,
  automation: openAutomation,
  settings: openSettings,
};
for (const b of document.querySelectorAll('button.navitem')) {
  b.onclick = () => {
    NAV_ACTIONS[b.dataset.nav]?.();
    if (phone.matches) closeDrawers();
  };
}
// Botões de atalho espalhados pela tela (estados vazios, página inicial): data-go="new|projects|jobs".
document.addEventListener('click', (e) => {
  const go = e.target.closest('[data-go]');
  if (go) NAV_ACTIONS[go.dataset.go]?.();
});
$('openCutsTop').onclick = () => openLeft('cuts');
$('openRights').onclick = () => $('rightsDialog').showModal();
$('help').onclick = () => $('helpDialog').showModal();
narrow.addEventListener('change', () => { if (!narrow.matches) document.body.dataset.left = 'closed'; syncScrim(); });
phone.addEventListener('change', syncScrim);

// ------------------------------------------------------------------ biblioteca e importação

const RECENT_PROJECTS = 8;
const JOB_STATUS = {queued: 'Na fila', running: 'Em andamento', cancelling: 'Cancelando', done: 'Concluído',
  failed: 'Falhou', cancelled: 'Cancelado', interrupted: 'Interrompido'};

// Página inicial inteira numa consulta: projetos, tarefas em andamento, exportações e dependências. Enquanto há
// algo rodando, atualiza sozinha a cada 5 s.
async function loadProjects() {
  $('projects').setAttribute('aria-busy', 'true');
  try {
    const d = await api('/api/dashboard');
    state.dashboard = d;
    clearTimeout(state.projectsTimer);
    const busy = d.active_jobs.length || d.projects.some((p) => p.analysis);
    if (busy) state.projectsTimer = setTimeout(loadProjects, 5000);
    renderProjects(d.projects);
    renderHomeJobs(d.active_jobs);
    renderExportsHome(d.recent_exports, d.output_dir);
    renderDeps($('homeDeps'), d.dependencies);
    $('homeLimits').textContent = 'MP4, MOV, MKV, WEBM ou AVI · até ' + d.limits.max_upload_gb + ' GB e ' +
      d.limits.max_source_hours + ' h';
    $('jobsBadge').hidden = !d.active_jobs.length;
    $('jobsBadge').textContent = d.active_jobs.length;
  } catch (e) {
    $('projects').innerHTML = '<p class="empty error">Não foi possível carregar os projetos. ' +
      'Verifique se o AutoCortes ainda está aberto e clique em atualizar.</p>';
    toast(e.message, true);
  } finally {
    $('projects').removeAttribute('aria-busy');
  }
}

function renderProjects(projects) {
  const list = state.projectsAll ? projects : projects.slice(0, RECENT_PROJECTS);
  $('projectsTitle').textContent = state.projectsAll ? 'Meus projetos' : 'Projetos recentes';
  $('projectsAll').hidden = state.projectsAll || projects.length <= RECENT_PROJECTS;
  $('projectsAll').textContent = 'Ver todos (' + projects.length + ')';
  $('projects').innerHTML = list.map((p) => {
    const selected = p.id === state.project?.id;
    const a = p.analysis;
    const status = a ? (a.status === 'queued' ? 'Na fila' : (STAGE_NAMES[a.stage] || 'Analisando')) + ' · ' +
      Math.round((a.progress || 0) * 100) + '%' : (PROJECT_STATUS[p.status] || p.status);
    const thumb = p.duration ? '<img class="projthumb" alt="" loading="lazy" src="/api/projects/' + escape(p.id) + '/thumb">' : '';
    return '<div class="projectrowitem"><button class="projectitem' + (selected ? ' selected' : '') + '" data-id="' +
      escape(p.id) + '"' + (selected ? ' aria-current="true"' : '') + '><span class="projmedia">' + thumb +
      '<svg class="ic"><use href="#i-film"/></svg></span><strong>' + escape(p.name) + '</strong><small>' +
      '<span class="dot" data-status="' + escape(p.status) + '"></span>' + escape(status) +
      ' · ' + (p.duration ? fmt(p.duration) + ' · ' : '') + p.clips + (p.clips === 1 ? ' corte' : ' cortes') +
      '</small></button>' + (STOPPED.includes(p.status)
        ? '<button class="iconbtn sm projredo" data-redo="' + escape(p.id) + '" data-name="' + escape(p.name) +
          '" title="Analisar de novo" aria-label="Analisar de novo o projeto ' + escape(p.name) +
          '"><svg class="ic"><use href="#i-refresh"/></svg></button>' : '') +
      '<button class="iconbtn sm projdel" data-del="' + escape(p.id) + '" data-name="' +
      escape(p.name) + '" title="Excluir projeto" aria-label="Excluir o projeto ' + escape(p.name) +
      '"><svg class="ic"><use href="#i-trash"/></svg></button></div>';
  }).join('') || '<div class="emptystate wide"><svg class="ic xl"><use href="#i-folder"/></svg><strong>Nenhum projeto ainda</strong>' +
    '<p>Arraste um vídeo para a área acima ou clique em <b>Criar cortes</b>.</p></div>';
  // Miniatura que não carregou (vídeo sem quadro legível): fica o ícone.
  for (const img of $('projects').querySelectorAll('.projthumb')) img.onerror = () => img.remove();
}

function jobRow(j, {project = true, actions = true} = {}) {
  const active = ACTIVE_JOB.includes(j.status);
  const what = j.kind_label + (j.clip_title ? ' · ' + j.clip_title : '') + (j.format ? ' · ' + j.format : '');
  const elapsed = j.started ? fmtDur((active ? Date.now() / 1000 : j.finished || Date.now() / 1000) - j.started) : '';
  const detail = active
    ? (j.status === 'queued' ? 'Aguardando a vez' : (j.message || JOB_STATUS[j.status])) + ' · ' +
      Math.round((j.progress || 0) * 100) + '%' + (elapsed ? ' · ' + elapsed : '')
    : JOB_STATUS[j.status] + (elapsed ? ' · ' + elapsed : '') + (j.error ? ' · ' + j.error : '');
  const buttons = !actions ? '' : (active && j.status !== 'cancelling'
    ? '<button type="button" class="btn ghost danger qcancel" data-jobcancel="' + j.id + '"><svg class="ic"><use href="#i-x"/></svg><span>Cancelar</span></button>' : '') +
    (['failed', 'interrupted'].includes(j.status) && ['export', 'analysis', 'download'].includes(j.kind) && !j.narration
      ? '<button type="button" class="btn ghost" data-retry="' + j.id + '">Tentar de novo</button>' : '') +
    (j.project_id && j.kind !== 'download' ? '<button type="button" class="btn ghost" data-jobopen="' + escape(j.project_id) + '">Abrir</button>' : '');
  return '<li class="jobrow" data-status="' + j.status + '"><div class="qmain"><strong>' + escape(project ? j.project_name : what) +
    '</strong><small>' + escape(project ? what + ' — ' + detail : detail) + '</small>' +
    (active ? '<progress max="100" value="' + Math.round((j.progress || 0) * 100) + '"></progress>' : '') +
    '</div><span class="qactions">' + buttons + '</span></li>';
}

function renderHomeJobs(jobs) {
  $('homeJobsSec').hidden = !jobs.length;
  $('homeJobs').innerHTML = jobs.slice(0, 5).map((j) => jobRow(j)).join('');
}

function renderExportsHome(items, outputDir) {
  $('homeExports').innerHTML = items.map((x) =>
    '<li class="exportrow"><div class="qmain"><strong>' + escape(x.title || 'Corte ' + x.clip_id) + '</strong><small>' +
    escape(x.project_name) + ' · ' + escape(x.format) + ' · ' + fmt(x.duration) + '</small></div><span class="qactions">' +
    '<button type="button" class="btn ghost" data-jobopen="' + escape(x.project_id) + '">Abrir</button>' +
    '<a class="iconbtn" title="Baixar MP4" aria-label="Baixar ' + escape(x.title || '') + '" href="' +
    downloadUrl(x.project_id, x.clip_id, x.format) + '"><svg class="ic"><use href="#i-download"/></svg></a></span></li>').join('') ||
    '<li class="emptyline">Os cortes que você exportar aparecem aqui, prontos para baixar.</li>';
  $('outputDir').textContent = outputDir ? 'Cópias também ficam na pasta de saída: ' + outputDir : '';
}

function renderDeps(box, deps) {
  box.innerHTML = deps.map((d) => '<li class="dep' + (d.ok ? ' ok' : ' bad') + '"><svg class="ic"><use href="#' +
    (d.ok ? 'i-check' : 'i-alert') + '"/></svg><div><strong>' + escape(d.label) + '</strong><small>' + escape(d.detail) +
    (d.ok ? (d.hint ? ' — ' + escape(d.hint) : '') : ' — ' + escape(d.fix)) + '</small></div></li>').join('');
}

// Ações das listas de tarefas (página inicial, Processamentos e painel de exportações).
document.addEventListener('click', async (e) => {
  const retry = e.target.closest('[data-retry]');
  if (retry) {
    retry.disabled = true;
    try {
      const j = await api('/api/jobs/' + retry.dataset.retry + '/retry', 'POST');
      toast(j.duplicate ? 'Essa exportação já está na fila.' : 'Tarefa enviada de novo para a fila.');
      if (state.project) { state.project.jobs = [j, ...(state.project.jobs || [])]; renderExports(); }
    } catch (err) { toast(err.message, true); retry.disabled = false; }
    if ($('jobsDialog').open) loadJobs();
    loadProjects();
    return;
  }
  const cancel = e.target.closest('[data-jobcancel]');
  if (cancel) {
    cancel.disabled = true;
    try {
      await api('/api/jobs/' + cancel.dataset.jobcancel + '/cancel', 'POST');
      toast('Tarefa cancelada.');
    } catch (err) { toast(err.message, true); }
    loadProjects();
    if ($('jobsDialog').open) loadJobs();
    return;
  }
  const open = e.target.closest('[data-jobopen]');
  if (open) {
    $('jobsDialog').close();
    openProject(open.dataset.jobopen).catch((err) => { setLoading(null); toast(err.message, true); });
  }
});

// ------------------------------------------------------------------ feedback e motivo da sugestão

function renderFeedback(c) {
  const verdict = c.feedback?.verdict || null;
  $('fbGood').setAttribute('aria-pressed', String(verdict === 'bom'));
  $('fbBad').setAttribute('aria-pressed', String(verdict === 'rejeitado'));
  const evidence = c.evidence || [], warnings = c.warnings || [];
  $('whyBox').hidden = !evidence.length && !warnings.length && !(c.reasons || []).length;
  $('whyEvidence').innerHTML = (evidence.length ? evidence.map((e) => '<li><b>' + escape(e.tipo) + '</b> · ' +
    escape(e.detalhe || '') + ' <small>(' + escape(e.origem) + ')</small></li>') : (c.reasons || []).map((r) =>
    '<li>' + escape(r) + '</li>')).join('') + ((c.sources || []).length ? '<li><small>Origem: ' +
    escape(c.sources.join(' + ')) + '</small></li>' : '');
  $('whyWarnings').innerHTML = warnings.map((w) => '<li>' + escape(w) + '</li>').join('');
}
async function sendFeedback(verdict) {
  const c = current();
  if (!c || !state.project) return;
  const next = c.feedback?.verdict === verdict ? null : verdict;   // clicar de novo desmarca
  try {
    const r = await api('/api/projects/' + state.project.id + '/clips/' + c.id + '/feedback', 'PUT', {verdict: next});
    c.feedback = r.feedback || undefined;
    render();
    if (next) toast((next === 'bom' ? 'Marcado como bom' : 'Rejeitado') + ' · ' + r.counts.bom + ' bons e ' +
      r.counts.rejeitado + ' rejeitados neste projeto.');
  } catch (e) { toast(e.message, true); }
}
$('fbGood').onclick = () => sendFeedback('bom');
$('fbBad').onclick = () => sendFeedback('rejeitado');
$('hideRejected').checked = store.get('hideRejected') === '1';
$('hideRejected').onchange = () => { store.set('hideRejected', $('hideRejected').checked ? '1' : '0'); render(); };

// ------------------------------------------------------------------ seleção múltipla e exportação em lote

const cutSort = () => (store.get('cutSort') === 'time' ? 'time' : 'score');
$('cutSort').value = cutSort();
$('cutSort').onchange = () => { store.set('cutSort', $('cutSort').value); render(); };

function syncPicked() {
  const clips = state.project?.clips || [];
  $('cutTools').hidden = !clips.length || !isReady();
  const n = state.picked.size;
  $('pickAll').checked = n > 0 && n === clips.length;
  $('pickAll').indeterminate = n > 0 && n < clips.length;
  $('exportPicked').disabled = !n;
  $('exportPickedLabel').textContent = n ? 'Exportar selecionados (' + n + ')' : 'Exportar selecionados';
}
$('pickAll').onchange = () => {
  state.picked = $('pickAll').checked ? new Set((state.project?.clips || []).map((c) => c.id)) : new Set();
  render();
};

const PRESETS = {
  shorts: {format: '9:16', max: 180, label: 'Shorts'},
  reels: {format: '9:16', max: 180, label: 'Reels'},
  tiktok: {format: '9:16', max: 600, label: 'TikTok'},
  horizontal: {format: '16:9', max: null, label: 'Horizontal'},
  original: {format: 'original', max: null, label: 'Original'},
};
const MBPS = 5;   // estimativa de taxa dos arquivos exportados (1080p H.264, medida nos cortes já feitos: 4–6 Mbit/s)
const presetInputs = () => [...document.querySelectorAll('.presetgrid input')];
function pickedPresets() { return presetInputs().filter((i) => i.checked).map((i) => i.value); }

function openBatch() {
  const p = state.project;
  if (!p || !state.picked.size) return;
  let saved = [];
  try { saved = JSON.parse(store.get('batch.presets') || '[]'); } catch { saved = []; }
  if (!saved.length) saved = ['shorts'];
  for (const i of presetInputs()) i.checked = saved.includes(i.value);
  const rights = !!p.rights?.requires_acknowledgement;
  $('batchRightsRow').hidden = !rights;
  $('batchRights').checked = false;
  const framing = $('framing').selectedOptions[0]?.textContent || $('framing').value;
  const filterName = state.filters.find((f) => f.id === state.filter)?.label;
  $('batchOptions').textContent = 'Usa as opções do editor: ' + [framing,
    $('subtitles').checked ? 'legenda ' + ($('subtitleStyle').value === 'dinamica' ? 'dinâmica' : 'clássica') : 'sem legenda',
    state.filter !== 'nenhum' && filterName ? 'filtro ' + filterName : null].filter(Boolean).join(' · ') + '.';
  syncBatch();
  $('batchDialog').showModal();
}
function syncBatch() {
  const clips = (state.project?.clips || []).filter((c) => state.picked.has(c.id));
  const presets = pickedPresets();
  const formats = [...new Set(presets.map((k) => PRESETS[k].format))];
  const files = clips.length * formats.length;
  const seconds = clips.reduce((s, c) => s + (c.end - c.start), 0) * formats.length;
  $('batchCount').textContent = clips.length + (clips.length === 1 ? ' corte selecionado' : ' cortes selecionados') +
    ' · ' + fmtDur(clips.reduce((s, c) => s + (c.end - c.start), 0)) + ' de vídeo';
  $('batchSummary').textContent = files
    ? files + (files === 1 ? ' arquivo' : ' arquivos') + ' (' + formats.join(', ') + ') · cerca de ' +
      fmtSize(seconds * MBPS * 1e6 / 8) + ' no total (estimativa)'
    : 'Escolha pelo menos um formato.';
  const warnings = [];
  for (const k of presets) {
    const max = PRESETS[k].max;
    const long = max ? clips.filter((c) => c.end - c.start > max).length : 0;
    if (long) warnings.push(long + (long === 1 ? ' corte passa' : ' cortes passam') + ' de ' + fmtDur(max) + ': o ' +
      PRESETS[k].label + ' pode recusar. Encurte antes de publicar lá.');
  }
  $('batchWarnings').innerHTML = warnings.map((w) => '<li class="warn">' + escape(w) + '</li>').join('');
  $('batchGo').disabled = !files;
  $('batchGo').textContent = files ? 'Exportar ' + files + (files === 1 ? ' arquivo' : ' arquivos') : 'Exportar';
}
for (const i of presetInputs()) i.addEventListener('change', syncBatch);
$('exportPicked').onclick = openBatch;

$('batchGo').onclick = async () => {
  const p = state.project;
  const presets = pickedPresets();
  const formats = [...new Set(presets.map((k) => PRESETS[k].format))];
  const rights = !!p.rights?.requires_acknowledgement;
  if (rights && !$('batchRights').checked) return toast('Confirme a Revisão de direitos para exportar.', true);
  store.set('batch.presets', JSON.stringify(presets));
  $('batchGo').disabled = true;
  try {
    const opts = exportOptions(formats[0], rights);
    delete opts.format;
    opts.edit_plan = null;   // o plano de edição é de um corte só; o lote usa as opções do editor
    const ids = (p.clips || []).filter((c) => state.picked.has(c.id)).map((c) => c.id);
    const res = await api('/api/projects/' + p.id + '/export-all', 'POST', {...opts, formats, clip_ids: ids});
    $('batchDialog').close();
    state.batch = {jobs: res.jobs.map((j) => j.id), order: ids, total: res.jobs.length, announced: false};
    state.project.jobs = [...res.jobs, ...(state.project.jobs || []).filter((j) => !res.jobs.some((x) => x.id === j.id))];
    renderExports();
    toast(res.jobs.length + ' exportações na fila' + (res.errors.length ? ' · ' + res.errors.length + ' não enviadas: ' + res.errors[0].error : '.'),
      !!res.errors.length);
    setStatus('Exportando em lote', 'busy');
  } catch (e) { toast(e.message, true); } finally { $('batchGo').disabled = false; }
};

// Andamento do lote pelas tarefas do projeto (atualizadas pela fila do painel de exportações).
function batchStatus(p) {
  const b = state.batch;
  if (!b) return null;
  const jobs = (p.jobs || []).filter((j) => b.jobs.includes(j.id));
  const done = jobs.filter((j) => j.status === 'done').length;
  const failed = jobs.filter((j) => FAILED_JOB.includes(j.status)).length;
  const running = jobs.find((j) => j.status === 'running');
  return {total: b.total, done, failed, progress: running?.progress || 0, finished: done + failed >= b.total,
    current: running ? (b.order.indexOf(running.clip_id) + 1) + ' de ' + b.order.length : null};
}
function batchPosition(j) {
  const b = state.batch;
  return b && b.jobs.includes(j.id) ? 'Corte ' + (b.order.indexOf(j.clip_id) + 1) + ' de ' + b.order.length + ' · ' : '';
}
function finishBatch(batch) {
  if (state.batch.announced) return;
  state.batch.announced = true;
  setStatus('Lote concluído', batch.failed ? 'error' : 'ok');
  toast('Lote concluído: ' + batch.done + ' de ' + batch.total + ' arquivos prontos' +
    (batch.failed ? '; ' + batch.failed + ' com erro (veja em Exportações).' : '. Use "Abrir pasta" para ver os arquivos.'),
    !!batch.failed);
  setTimeout(() => { if (state.batch?.announced) { state.batch = null; renderExports(); } }, 15000);
}

// ------------------------------------------------------------------ pastas

$('openFolder').onclick = async () => {
  try { const r = await api('/api/projects/' + state.project.id + '/open-folder', 'POST'); toast('Pasta aberta: ' + r.folder); }
  catch (e) { toast(e.message, true); }
};
$('openOutput').onclick = async () => {
  try { const r = await api('/api/open-output', 'POST'); toast('Pasta aberta: ' + r.folder); } catch (e) { toast(e.message, true); }
};

// ------------------------------------------------------------------ processamentos

async function loadJobs() {
  try {
    const d = await api('/api/jobs?limit=40');
    $('jobsList').innerHTML = d.jobs.map((j) => jobRow(j)).join('') ||
      '<li class="emptyline">Nenhum processamento ainda. Análises e exportações aparecem aqui.</li>';
    clearTimeout(state.jobsTimer);
    if ($('jobsDialog').open && d.jobs.some((j) => ACTIVE_JOB.includes(j.status))) state.jobsTimer = setTimeout(loadJobs, 2000);
  } catch (e) {
    $('jobsList').innerHTML = '<li class="emptyline error">' + escape(e.message) + '</li>';
  }
}
function openJobs() {
  loadJobs();
  $('jobsDialog').showModal();
}
$('jobsDialog').addEventListener('close', () => clearTimeout(state.jobsTimer));

// ------------------------------------------------------------------ configurações

const PREFS = {
  mode: () => ['automatic', 'manual', 'advanced'].includes(store.get('pref.mode')) ? store.get('pref.mode') : 'automatic',
  target: () => clamp(Number(store.get('pref.target')) || 35, 10, 90),
};
async function openSettings() {
  $('prefMode').value = PREFS.mode();
  $('prefTarget').value = PREFS.target();
  $('prefSubtitle').value = $('subtitleStyle').value;
  $('prefRefine').checked = $('refineSubtitles').checked;
  $('settingsDialog').showModal();
  try {
    const [cfg, d] = await Promise.all([api('/api/settings'), state.dashboard ? state.dashboard : api('/api/dashboard')]);
    $('prefGpuExport').checked = (cfg.export?.encoder || 'auto') === 'auto';
    $('settingsPaths').innerHTML = '<dt>Pasta monitorada (entrada)</dt><dd>' + escape(cfg.paths.watch_dir) + '</dd>' +
      '<dt>Pasta de saída</dt><dd>' + escape(cfg.paths.output_dir) + '</dd>';
    renderDeps($('settingsDeps'), d.dependencies);
  } catch (e) { toast(e.message, true); }
}
$('settingsSave').onclick = async () => {
  try {
    await api('/api/settings', 'PUT', {export: {encoder: $('prefGpuExport').checked ? 'auto' : 'x264'}});
  } catch (e) { toast(e.message, true); }
  store.set('pref.mode', $('prefMode').value);
  store.set('pref.target', String(clamp(Number($('prefTarget').value) || 35, 10, 90)));
  $('subtitleStyle').value = $('prefSubtitle').value;
  $('subtitleStyle').dispatchEvent(new Event('change'));
  $('refineSubtitles').checked = $('prefRefine').checked;
  $('refineSubtitles').dispatchEvent(new Event('change'));
  $('settingsDialog').close();
  toast('Preferências salvas.');
};
$('settingsSelection').onclick = () => { $('settingsDialog').close(); openSelection(); };

$('projects').onclick = (e) => {
  const del = e.target.closest('.projdel');
  if (del) return deleteProject(del.dataset.del, del.dataset.name);
  const redo = e.target.closest('.projredo');
  if (redo) return reanalyzeProject(redo.dataset.redo, {ready: false});
  const item = e.target.closest('.projectitem');
  if (!item) return;
  openProject(item.dataset.id).catch((err) => { setLoading(null); toast(err.message, true); });
};

const sourceMode = () => document.querySelector('[name=sourceMode]:checked').value;
const editMode = () => document.querySelector('[name=editMode]:checked')?.value || 'automatic';
for (const r of document.querySelectorAll('[name=sourceMode]')) {
  r.onchange = () => {
    $('fileWrap').hidden = sourceMode() !== 'file';
    $('pathWrap').hidden = sourceMode() !== 'path';
    $('urlWrap').hidden = sourceMode() !== 'link';
    if (sourceMode() === 'path') $('videoPath').focus();
  };
}
for (const r of document.querySelectorAll('[name=editMode]')) r.onchange = syncMode;
function syncMode() {
  const mode = editMode();
  $('advancedOptions').hidden = mode !== 'advanced';
  $('importButton').textContent = mode === 'manual' ? 'Abrir no editor' : 'Criar cortes';
}

function wizardStep(n) {
  $('step1').hidden = n !== 1;
  $('step2').hidden = n !== 2;
  $('stepDot1').classList.toggle('on', n === 1);
  $('stepDot2').classList.toggle('on', n === 2);
  $('stepBack').hidden = n !== 2;
  $('stepNext').hidden = n !== 1;
  $('importButton').hidden = n !== 2;
  (n === 1 ? $('pickFile') : document.querySelector('[name=editMode]:checked'))?.focus();
}

// Passo 1: o navegador lê duração e resolução do arquivo sem enviá-lo; os limites vêm do servidor.
const VIDEO_EXT = ['mp4', 'mov', 'mkv', 'webm', 'avi', 'm4v'];
function describeFile() {
  const f = $('file').files[0];
  $('filename').textContent = f ? f.name : 'Nenhum arquivo selecionado';
  const box = $('fileInfo');
  state.fileProblems = [];
  if (!f) { box.hidden = true; return; }
  const limits = state.dashboard?.limits || {};
  const ext = (f.name.split('.').pop() || '').toLowerCase();
  const problems = state.fileProblems;
  if (!VIDEO_EXT.includes(ext)) problems.push(['bad', 'Formato .' + ext + ' não é aceito. Converta para MP4 e tente de novo.']);
  if (limits.max_upload_gb && f.size > limits.max_upload_gb * 1024 ** 3) {
    problems.push(['bad', 'O arquivo passa de ' + limits.max_upload_gb + ' GB. Aumente MAX_UPLOAD_GB no iniciar_windows.bat.']);
  }
  const facts = [['Tamanho', fmtSize(f.size)]];
  const show = (extra = []) => {
    box.hidden = false;
    box.innerHTML = '<dl>' + facts.concat(extra).map(([k, v]) => '<dt>' + escape(k) + '</dt><dd>' + escape(v) + '</dd>').join('') +
      '</dl>' + (problems.length ? '<ul class="fileproblems">' + problems.map(([tone, t]) =>
        '<li class="' + tone + '">' + escape(t) + '</li>').join('') + '</ul>' : '<p class="fileok">Tudo certo com o arquivo.</p>');
  };
  show([['Duração', 'lendo…']]);
  const probe = document.createElement('video');
  probe.preload = 'metadata';
  const url = URL.createObjectURL(f);
  const done = () => URL.revokeObjectURL(url);
  probe.onloadedmetadata = () => {
    const dur = probe.duration;
    const extra = [['Duração', isFinite(dur) ? fmt(dur) : 'desconhecida'],
      ['Resolução', probe.videoWidth ? probe.videoWidth + '×' + probe.videoHeight : 'desconhecida']];
    if (limits.max_source_hours && dur > limits.max_source_hours * 3600) {
      problems.push(['bad', 'O vídeo passa de ' + limits.max_source_hours + ' h. Aumente AUTOCORTES_MAX_SOURCE_HOURS ou corte em partes.']);
    }
    if (probe.videoWidth && Math.min(probe.videoWidth, probe.videoHeight) < 480) {
      problems.push(['warn', 'Resolução baixa: os cortes verticais podem ficar sem nitidez.']);
    }
    if (dur > 2 * 3600) problems.push(['info', 'Vídeo longo: a análise pode levar bastante tempo; o andamento aparece na página inicial.']);
    show(extra);
    done();
    checkDuplicate(f, () => show(extra));
  };
  probe.onerror = () => {
    const extra = [['Duração', 'o navegador não leu; o AutoCortes verifica ao importar']];
    show(extra);
    done();
    checkDuplicate(f, () => show(extra));
  };
  probe.src = url;
}
// Impressão rápida: tamanho + SHA-256 de três blocos de 4 MB (início, meio, fim), igual a service.quick_fingerprint.
async function quickFingerprint(file) {
  const M = 4 * 1024 * 1024;
  const half = Math.floor(file.size / 2);
  const parts = file.size <= 3 * M ? [file] : [file.slice(0, M), file.slice(half - M / 2, half + M / 2), file.slice(file.size - M)];
  const digest = await crypto.subtle.digest('SHA-256', await new Blob(parts).arrayBuffer());
  return 'quick:' + file.size + ':' + [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}
async function checkDuplicate(file, redraw) {
  state.duplicateOf = null;
  try {
    const quick = await quickFingerprint(file);
    if ($('file').files[0] !== file) return;
    const {duplicate} = await api('/api/projects/check-duplicate', 'POST', {size: file.size, quick});
    if (!duplicate || $('file').files[0] !== file) return;
    state.duplicateOf = duplicate;
    state.fileProblems.push(['warn', 'Este vídeo já está no projeto "' + duplicate.name + '" (' + (duplicate.clips || 0) +
      ' cortes). Importar de novo ocupa mais ' + fmtSize(file.size) + '.']);
    redraw();
    const open = document.createElement('button');
    open.type = 'button';
    open.className = 'btn';
    open.dataset.jobopen = duplicate.id;
    open.textContent = 'Abrir o projeto existente';
    open.onclick = () => $('newDialog').close();
    $('fileInfo').append(open);
  } catch { /* sem a verificação, a importação segue normal */ }
}
$('file').onchange = describeFile;
$('pickFile').onclick = () => $('file').click();

function openNewProject(file = null) {
  $('uploadForm').reset();
  for (const r of document.querySelectorAll('[name=editMode]')) r.checked = r.value === PREFS.mode();
  $('target').value = PREFS.target();
  $('linkOption').hidden = !state.dashboard?.links_enabled;
  $('fileWrap').hidden = false;
  $('pathWrap').hidden = true;
  $('pathInfo').hidden = true;
  state.pathInfo = null;
  $('urlWrap').hidden = true;
  if (file) {
    const dt = new DataTransfer();
    dt.items.add(file);
    $('file').files = dt.files;
  }
  describeFile();
  syncMode();
  wizardStep(1);
  $('newDialog').showModal();
  if (!state.dashboard) loadProjects();
}
// Vídeo que já está no computador: o servidor (local) confere o arquivo, se vai copiar e se já existe num projeto.
async function checkPath() {
  const path = $('videoPath').value.trim();
  state.pathInfo = null;
  if (!path) { $('pathInfo').hidden = true; return null; }
  const box = $('pathInfo');
  box.hidden = false;
  box.textContent = 'Verificando o arquivo…';
  try {
    const info = await api('/api/projects/check-path', 'POST', {path});
    if ($('videoPath').value.trim() !== path) return null;
    state.pathInfo = info;
    const lines = [];
    if (info.too_big) lines.push(['bad', 'O arquivo passa do limite de envio. Aumente MAX_UPLOAD_GB no iniciar_windows.bat.']);
    lines.push(info.no_copy ? ['ok', 'Mesmo disco do AutoCortes: entra na hora, sem copiar e sem ocupar espaço extra.']
      : ['info', 'Outro disco: o vídeo será copiado para a pasta do AutoCortes (bem mais rápido que enviar).']);
    if (info.duplicate) lines.push(['warn', 'Este vídeo já está no projeto "' + info.duplicate.name + '" (' +
      (info.duplicate.clips || 0) + ' cortes).']);
    box.innerHTML = '<dl><dt>Arquivo</dt><dd>' + escape(info.name) + '</dd><dt>Tamanho</dt><dd>' +
      fmtSize(info.size_bytes) + '</dd></dl><ul class="fileproblems">' +
      lines.map(([tone, t]) => '<li class="' + tone + '">' + escape(t) + '</li>').join('') + '</ul>';
    if (info.duplicate) {
      const open = document.createElement('button');
      open.type = 'button';
      open.className = 'btn';
      open.dataset.jobopen = info.duplicate.id;
      open.textContent = 'Abrir o projeto existente';
      open.onclick = () => $('newDialog').close();
      box.append(open);
    }
    return info;
  } catch (e) {
    box.innerHTML = '<ul class="fileproblems"><li class="bad">' + escape(e.message) + '</li></ul>';
    return null;
  }
}
let pathTimer = null;
$('videoPath').addEventListener('input', () => { clearTimeout(pathTimer); pathTimer = setTimeout(checkPath, 500); });

$('stepNext').onclick = async () => {
  if (sourceMode() === 'path') {
    const info = state.pathInfo || await checkPath();
    if (!info) return toast('Confira o caminho do vídeo.', true);
    if (info.too_big) return toast('O arquivo passa do limite de envio.', true);
    if (info.duplicate && !await askConfirm({
      title: 'Vídeo já importado', yes: 'Importar mesmo assim',
      text: 'Este vídeo já está no projeto "' + info.duplicate.name + '". Importar de novo refaz a análise.',
    })) return;
    return wizardStep(2);
  }
  if (sourceMode() === 'file' && !$('file').files[0]) return toast('Selecione um vídeo para continuar.', true);
  if (sourceMode() === 'link' && !$('videoUrl').value.trim()) return toast('Cole o link do vídeo.', true);
  if ((state.fileProblems || []).some(([tone]) => tone === 'bad')) return toast('Resolva o problema do arquivo antes de continuar.', true);
  if (state.duplicateOf && sourceMode() === 'file' && !await askConfirm({
    title: 'Vídeo já importado', yes: 'Importar mesmo assim',
    text: 'Este vídeo já está no projeto "' + state.duplicateOf.name + '". Importar de novo ocupa mais ' +
      fmtSize(state.duplicateOf.size_bytes) + ' e refaz a análise. Para usar o que já existe, feche e abra o projeto.',
  })) return;
  wizardStep(2);
};
$('stepBack').onclick = () => wizardStep(1);

for (const zone of [$('dropZone'), $('homeDrop')]) {
  zone.ondragover = (e) => { e.preventDefault(); zone.classList.add('over'); };
  zone.ondragleave = () => zone.classList.remove('over');
  zone.ondrop = (e) => {
    e.preventDefault();
    zone.classList.remove('over');
    const file = e.dataTransfer.files[0];
    if (!file) return;
    if (zone.id === 'homeDrop') return openNewProject(file);
    $('file').files = e.dataTransfer.files;
    describeFile();
  };
}
$('homeDrop').onclick = () => { openNewProject(); $('file').click(); };
$('homeDrop').onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('homeDrop').click(); } };
$('projectsAll').onclick = () => NAV_ACTIONS.projects();

function params() {
  return {
    source_rights: $('sourceRights').value, third_party_footage: $('thirdParty').checked ? 'yes' : 'unknown',
    external_audio: $('audioRights').value, graphics_rights: $('graphicsRights').value,
    monetization: $('monetization').value, license_notes: $('licenseNotes').value,
    editing_mode: editMode() === 'manual' ? 'manual' : 'automatic',  // avançado = automático com os ajustes à mostra
    target_seconds: $('target').value, desired_count: 0,  // 0: todos os trechos válidos
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
  if (sourceMode() === 'path' && !$('videoPath').value.trim()) return toast('Informe o caminho do vídeo.', true);
  btn.disabled = true;
  btn.setAttribute('aria-busy', 'true');
  store.set('pref.mode', editMode());
  state.pendingProfile = $('contentProfile').value || null;
  store.set('pref.contentProfile', $('contentProfile').value);
  if (editMode() === 'advanced') store.set('pref.target', String(clamp(Number($('target').value) || 35, 10, 90)));
  try {
    if (sourceMode() === 'path') {
      setStatus('Importando do computador…', 'busy');
      $('importButton').textContent = state.pathInfo?.no_copy ? 'Importando…' : 'Copiando o vídeo…';
      const d = await api('/api/projects/from-path', 'POST', {path: $('videoPath').value.trim(), options: p});
      $('newDialog').close();
      toast(d.no_copy ? 'Vídeo importado sem cópia (mesmo disco).' : 'Vídeo copiado e importado.');
      await openProject(d.id);
    } else if (sourceMode() === 'link') {
      setStatus('Baixando vídeo…', 'busy');
      setLoading('Baixando vídeo…');
      const result = await api('/api/imports/link', 'POST', {url: $('videoUrl').value.trim(), options: p});
      toast('Importação adicionada à fila.');
      $('newDialog').close();
      await waitImport(result.id);
    } else {
      setStatus('Enviando vídeo…', 'busy');
      const d = await uploadFile(f, p);
      $('newDialog').close();
      await openProject(d.id);
    }
  } catch (err) {
    state.pendingProfile = null;
    setLoading(null);
    setStatus('Falha na importação', 'error');
    toast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.removeAttribute('aria-busy');
    $('uploadProgress').hidden = true;
    syncMode();   // volta o texto do botão
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
    state.picked = new Set();
    state.batch = null;
    state.markIn = 0;
    state.markOut = Math.min(30, mediaDuration());
    setThumb(null);
    setDirty(false);
    hideExport();
    readLabel();
    video.src = '/api/projects/' + id + '/source';
    history.replaceState({}, '', '/?projeto=' + id);
    // Perfil do projeto: o tipo de conteúdo escolhido ao criar, ou o último perfil usado nele.
    if (state.pendingProfile) store.set('profile.' + id, state.pendingProfile);
    state.pendingProfile = null;
    applyStoredProfile();
  } else if (!p.clips.some((c) => c.id === state.selected)) {
    state.selected = p.clips[0]?.id || null;
  }
  $('noVideo').hidden = true;
  setView('editor');
  if (changed) computeScale();
  $('mediaEmpty').hidden = true;
  $('openRights').hidden = false;
  $('projectName').textContent = $('mediaName').textContent = $('crumb').textContent = p.name;
  document.title = p.name + ' — AutoCortes Studio';
  const m = p.media;
  $('projectMeta').textContent = fmt(mediaDuration()) + ' · ' + m.height + 'p';
  $('mediaMeta').textContent = [fmt(mediaDuration()), m.width + '×' + m.height, m.fps ? Math.round(m.fps) + ' fps' : null,
    m.size_bytes ? fmtSize(m.size_bytes) : null, p.editing_mode === 'manual' ? 'Edição manual' : 'Seleção automática']
    .filter(Boolean).join(' · ');
  $('mediaCard').hidden = false;
  $('openSelection').hidden = !(p.status === 'ready' && p.transcription?.state === 'ok');
  $('reanalyze').hidden = !(p.status === 'ready' || STOPPED.includes(p.status));
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
    if (filtersVisible()) loadFilterSamples();
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
  $('mediaEmpty').hidden = false;
  $('openRights').hidden = true;
  $('cutTools').hidden = true;
  $('openSelection').hidden = true;
  $('reanalyze').hidden = true;
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
  setView('home');
}

// Projetos cuja análise parou (interrompida, com erro ou cancelada): a lista oferece "Analisar de novo".
const STOPPED = ['interrupted', 'failed', 'error', 'cancelled'];

async function reanalyzeProject(id, {ready}) {
  if (ready && !await askConfirm({
    title: 'Analisar de novo', yes: 'Analisar de novo',
    text: 'O vídeo será analisado do zero: transcrição, cenas e escolha dos cortes. Os ' +
      (state.project?.clips?.length || 0) + ' cortes atuais serão substituídos quando terminar (se a análise ' +
      'falhar, eles continuam). Para só escolher os cortes de novo, sem transcrever, use “Seleção de cortes e IA”.',
  })) return;
  try {
    await api('/api/projects/' + id + '/reanalyze', 'POST');
    toast('Análise reiniciada.');
    loadProjects();
    await openProject(id);
  } catch (e) { toast(e.message, true); }
}
$('reanalyze').onclick = () => {
  if (state.project) reanalyzeProject(state.project.id, {ready: state.project.status === 'ready'});
};

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
  if (c.feedback?.verdict === 'bom') out.push('👍 Bom');
  if (c.feedback?.verdict === 'rejeitado') out.push('👎 Rejeitado');
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
  renderFeedback(c);
}

function render() {
  const p = state.project;
  if (!p) return updateControls();
  const c = current();
  $('count').textContent = p.clips.length;
  renderExports();
  let ordered = cutSort() === 'time' ? [...p.clips].sort((a, b) => a.start - b.start) : p.clips;
  if ($('hideRejected').checked) ordered = ordered.filter((x) => x.feedback?.verdict !== 'rejeitado' || x.id === state.selected);
  for (const id of [...state.picked]) if (!p.clips.some((x) => x.id === id)) state.picked.delete(id);
  $('cutCards').innerHTML = ordered.map((x) =>
    '<div class="cutRow"><label class="pick" title="Selecionar para exportar"><input type="checkbox" data-pick="' + x.id + '"' +
    (state.picked.has(x.id) ? ' checked' : '') + ' aria-label="Selecionar ' + escape(x.title) + '"></label>' +
    '<button class="cutCard' + (x.id === state.selected ? ' selected' : '') + '" data-id="' + x.id + '"' +
    (x.id === state.selected ? ' aria-current="true"' : '') + '><strong>' + escape(x.title) + '</strong><small>' +
    fmt(x.start) + ' — ' + fmt(x.end) + ' · ' + fmt(x.end - x.start) + '</small><span class="badges">' +
    clipBadges(x).map((b) => '<em' + (b.startsWith('Exportado') ? ' class="done"' : '') + '>' + escape(b) + '</em>').join('') +
    '</span></button></div>').join('') ||
    '<p class="empty">Nenhum corte ainda. Marque a entrada (I) e a saída (O) e use Cortar.</p>';
  syncPicked();
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
  streamer: 'Só o streamer', split: 'Webcam + jogo'};
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

// ------------------------------------------------------------------ seleção de cortes e curadoria com IA

async function openSelection() {
  const dlg = $('selectionDialog');
  try {
    const [cfg, health] = await Promise.all([api('/api/settings'), api('/api/health')]);
    const cur = cfg.curation, providers = health.curation || {};
    $('curationProvider').innerHTML = Object.entries(providers).map(([id, info]) =>
      '<option value="' + id + '"' + (id === cur.provider ? ' selected' : '') + '>' + escape(info.label) +
      (id !== 'heuristica' && !info.configured ? ' — sem chave' : '') + '</option>').join('');
    $('curationProvider').dataset.providers = JSON.stringify(providers);
    $('curationConsent').checked = !!cur.consent;
    $('curationPrompt').value = cur.prompt || '';
    syncCuration();
    const clips = state.project?.clips || [];
    const exported = clips.filter((c) => Object.keys(c.exports || {}).length).length;
    $('reselectNote').textContent = 'Refazer a seleção usa a transcrição já salva (não transcreve de novo) e substitui os ' +
      clips.length + ' cortes atuais' + (exported ? ', incluindo ' + exported + ' já exportado(s) — baixe-os antes' : '') + '.';
    dlg.showModal();
  } catch (e) { toast(e.message, true); }
}

function syncCuration() {
  const id = $('curationProvider').value;
  const info = JSON.parse($('curationProvider').dataset.providers || '{}')[id] || {};
  const cloud = id !== 'heuristica';
  $('curationConsentRow').hidden = !cloud;
  $('curationKeyNote').hidden = !cloud;
  const env = id === 'anthropic' ? 'ANTHROPIC_API_KEY' : 'OPENAI_API_KEY';
  $('curationKeyNote').textContent = info.configured
    ? 'Chave encontrada. Modelo: ' + (info.model || '—') + '.'
    : 'Chave não encontrada: defina ' + env + ' no Windows e reinicie o AutoCortes.';
  $('curationKeyNote').classList.toggle('warn', cloud && !info.configured);
  $('reselect').disabled = cloud && !info.configured;  // sem chave a IA falharia e voltaria à heurística
}
$('curationProvider').addEventListener('change', syncCuration);

async function saveCuration() {
  const provider = $('curationProvider').value;
  await api('/api/settings', 'PUT', {curation: {
    provider, consent: provider !== 'heuristica' && $('curationConsent').checked, prompt: $('curationPrompt').value.trim(),
  }});
}
$('curationSave').onclick = async () => {
  try { await saveCuration(); toast('Configuração da seleção salva.'); $('selectionDialog').close(); } catch (e) { toast(e.message, true); }
};
$('reselect').onclick = async () => {
  const p = state.project;
  if (!p) return;
  try {
    await saveCuration();
    await api('/api/projects/' + p.id + '/reselect', 'POST');
    $('selectionDialog').close();
    toast('Refazendo a seleção com a transcrição salva…');
    await openProject(p.id);
  } catch (e) { toast(e.message, true); }
};
$('openSelection').onclick = openSelection;

function select(id, {seek = true} = {}) {
  state.selected = id;
  const c = current();
  if (c) {
    state.markIn = c.start;
    state.markOut = c.end;
    if (seek && state.project) video.currentTime = c.start;
  }
  render();
  if (filtersVisible()) loadFilterSamples();
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
  const pick = e.target.closest('[data-pick]');
  if (pick) {
    const id = Number(pick.dataset.pick);
    if (pick.checked) state.picked.add(id); else state.picked.delete(id);
    return syncPicked();
  }
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

// ------------------------------------------------------------------ perfis de exportação

// Prontos (tipo de conteúdo, vêm do servidor) + salvos pelo usuário em Configurações. O perfil escolhido fica
// lembrado por projeto; mudar qualquer opção à mão volta para "Personalizado".
state.profiles = {builtin: [], saved: []};
const profileKey = (p) => (p.builtin ? 'b:' + p.id : 'u:' + p.name);
const allProfiles = () => [...state.profiles.builtin, ...state.profiles.saved];
const findProfile = (key) => allProfiles().find((p) => profileKey(p) === key);

function currentOptions() {
  return {format: state.format, framing: $('framing').value, subtitles: $('subtitles').checked,
    subtitle_style: $('subtitleStyle').value, refine_subtitles: $('refineSubtitles').checked,
    normalize_audio: $('normalize').checked, hook: $('hook').checked,
    filter: state.filter === 'nenhum' ? null : state.filter};
}
const PROFILE_FIELDS = ['format', 'framing', 'subtitles', 'subtitle_style', 'refine_subtitles', 'normalize_audio',
  'hook', 'filter'];
const sameOptions = (p) => { const o = currentOptions(); return PROFILE_FIELDS.every((k) => (p[k] ?? null) === o[k]); };

function renderProfiles() {
  const opt = (p) => '<option value="' + escape(profileKey(p)) + '">' + escape(p.name) + '</option>';
  const keep = $('profileSel').value;
  $('profileSel').innerHTML = '<option value="">Personalizado</option>' +
    '<optgroup label="Por tipo de conteúdo">' + state.profiles.builtin.map(opt).join('') + '</optgroup>' +
    (state.profiles.saved.length ? '<optgroup label="Meus perfis">' + state.profiles.saved.map(opt).join('') +
      '</optgroup>' : '');
  $('profileSel').value = findProfile(keep) ? keep : '';
  $('contentProfile').innerHTML = '<option value="">Escolher depois</option>' + allProfiles().map(opt).join('');
  $('contentProfile').value = findProfile(store.get('pref.contentProfile')) ? store.get('pref.contentProfile') : '';
  syncProfileButtons();
}
function syncProfileButtons() {
  const p = findProfile($('profileSel').value);
  $('profileDelete').hidden = !p || p.builtin;
}
async function loadProfiles() {
  try {
    const s = await api('/api/settings');
    state.profiles = {builtin: s.builtin_profiles || [], saved: s.export_profiles || []};
  } catch { /* sem perfis: o painel continua funcionando no modo personalizado */ }
  renderProfiles();
  applyStoredProfile();
}
function applyStoredProfile() {
  const key = state.project && store.get('profile.' + state.project.id);
  if (key && !applyProfile(key)) $('profileSel').value = '';
  else if (!key) { $('profileSel').value = ''; syncProfileButtons(); }
}

state.applyingProfile = false;
function applyProfile(key) {
  const p = findProfile(key);
  if (!p) return false;
  state.applyingProfile = true;
  try {
    document.querySelector('[data-format="' + p.format + '"]')?.click();
    const set = (id, prop, value) => { $(id)[prop] = value; $(id).dispatchEvent(new Event('change')); };
    set('framing', 'value', p.framing);
    set('subtitles', 'checked', p.subtitles);
    set('subtitleStyle', 'value', p.subtitle_style);
    set('refineSubtitles', 'checked', p.refine_subtitles);
    set('normalize', 'checked', p.normalize_audio);
    set('hook', 'checked', p.hook);
    state.filter = p.filter || 'nenhum';
    store.set('filter', state.filter);
    applyFilter();
  } finally { state.applyingProfile = false; }
  $('profileSel').value = key;
  if (state.project) store.set('profile.' + state.project.id, key);
  syncProfileButtons();
  syncSubtitleStyle();
  return true;
}
// Mudou uma opção à mão: o perfil deixa de valer (a menos que tudo continue igual).
function profileTouched() {
  if (state.applyingProfile || !$('profileSel').value) return;
  const p = findProfile($('profileSel').value);
  if (p && sameOptions(p)) return;
  $('profileSel').value = '';
  if (state.project) store.set('profile.' + state.project.id, '');
  syncProfileButtons();
}
for (const id of ['framing', 'subtitles', 'subtitleStyle', 'refineSubtitles', 'normalize', 'hook']) {
  $(id).addEventListener('change', profileTouched);
}
for (const b of document.querySelectorAll('[data-format]')) b.addEventListener('click', () => setTimeout(profileTouched));
$('filterGrid').addEventListener('click', () => setTimeout(profileTouched));

$('profileSel').onchange = () => {
  const key = $('profileSel').value;
  if (key) { applyProfile(key); toast('Perfil "' + findProfile(key).name + '" aplicado.'); }
  else { if (state.project) store.set('profile.' + state.project.id, ''); syncProfileButtons(); }
};
const FRAMING_NAMES = {fit: 'quadro inteiro', blur: 'fundo desfocado', track: 'seguir rosto', streamer: 'só o streamer',
  split: 'webcam + jogo', mix: 'por trecho'};
$('profileSave').onclick = () => {
  const o = currentOptions();
  $('profileSummary').textContent = [o.format, FRAMING_NAMES[o.framing] || o.framing,
    o.subtitles ? 'legenda ' + (o.subtitle_style === 'dinamica' ? 'animada' : 'clássica') : 'sem legenda',
    o.filter ? 'filtro ' + (state.filters.find((f) => f.id === o.filter)?.label || o.filter) : null,
    o.normalize_audio ? 'volume normalizado' : null, o.hook ? 'gancho' : null].filter(Boolean).join(' · ');
  $('profileName').value = '';
  $('profileDialog').showModal();
  $('profileName').focus();
};
$('profileForm').onsubmit = async (e) => {
  e.preventDefault();
  const name = $('profileName').value.trim().replace(/\s+/g, ' ');
  if (!name) return;
  const saved = state.profiles.saved.filter((p) => p.name.toLowerCase() !== name.toLowerCase());
  const replacing = saved.length < state.profiles.saved.length;
  try {
    const s = await api('/api/settings', 'PUT', {export_profiles: [...saved, {name, ...currentOptions()}]});
    state.profiles.saved = s.export_profiles;
    $('profileDialog').close();
    renderProfiles();
    applyProfile('u:' + s.export_profiles.find((p) => p.name.toLowerCase() === name.toLowerCase()).name);
    toast(replacing ? 'Perfil "' + name + '" atualizado.' : 'Perfil "' + name + '" salvo.');
  } catch (err) { toast(err.message, true); }
};
$('profileDelete').onclick = async () => {
  const p = findProfile($('profileSel').value);
  if (!p || p.builtin) return;
  if (!await askConfirm({title: 'Excluir perfil', text: 'Excluir o perfil "' + p.name + '"? Os cortes já exportados não mudam.',
    yes: 'Excluir', danger: true})) return;
  try {
    const s = await api('/api/settings', 'PUT', {export_profiles: state.profiles.saved.filter((x) => x !== p)});
    state.profiles.saved = s.export_profiles;
    $('profileSel').value = '';
    renderProfiles();
    toast('Perfil excluído.');
  } catch (err) { toast(err.message, true); }
};
loadProfiles();

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
  clearTimeout(state.queueTimer);
  state.queueTimer = null;
  state.queueClosed = {};
  $('exportQueue').replaceChildren();
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
    edit_plan: exportPlanName(), filter: state.filter === 'nenhum' ? null : state.filter,
  };
}

// ------------------------------------------------------------------ filtro de cor

// Amostras: um quadro do vídeo em cada filtro, lado a lado numa tira gerada pelo servidor (FFmpeg, exato).
// O player usa a aproximação em CSS para mostrar o filtro enquanto o vídeo toca.
state.filters = [];
state.filter = store.get('filter') || 'nenhum';
state.filterKey = null;

async function loadFilterCatalog() {
  try { state.filters = (await api('/api/filters')).filters; } catch { state.filters = []; }
  if (!state.filters.some((f) => f.id === state.filter)) state.filter = 'nenhum';
  applyFilter();
  if (filtersVisible()) loadFilterSamples();
}
const filtersVisible = () => !$('panelEffects').hidden;

function applyFilter() {
  const f = state.filters.find((x) => x.id === state.filter);
  video.style.filter = f?.css || '';
  $('filterDesc').textContent = f ? f.label + ': ' + f.desc : '';
  for (const b of $('filterGrid').querySelectorAll('[data-filter]')) {
    const on = b.dataset.filter === state.filter;
    b.classList.toggle('selected', on);
    b.setAttribute('aria-checked', on);
  }
}

function loadFilterSamples(force = false) {
  const p = state.project;
  if (!p || p.status !== 'ready' || !state.filters.length) return;
  const c = current();
  // Instante: o meio do corte selecionado; com "Ver exemplos no quadro atual", onde o player está.
  let t = force || !c ? video.currentTime || 0 : (c.start + c.end) / 2;
  t = Math.round(t * 10) / 10;
  const key = p.id + ':' + t;
  if (key === state.filterKey && !force) return;
  state.filterKey = key;
  const url = '/api/projects/' + p.id + '/filter-samples?t=' + t;
  const n = state.filters.length;
  const ratio = (p.media.width / p.media.height) || 16 / 9;
  $('filterGrid').innerHTML = state.filters.map((f, k) =>
    '<button type="button" role="radio" class="filtertile" data-filter="' + f.id + '" title="' + escape(f.desc) + '">' +
    '<span class="filterthumb loading"></span><span class="filtername">' + escape(f.label) + '</span></button>').join('');
  // Estilos pelo CSSOM: a CSP da página bloqueia o atributo style escrito no HTML.
  [...$('filterGrid').querySelectorAll('.filterthumb')].forEach((s, k) => {
    s.style.aspectRatio = ratio.toFixed(3);
    s.style.backgroundImage = 'url("' + url + '")';
    s.style.backgroundSize = (n * 100) + '% 100%';
    s.style.backgroundPosition = (n > 1 ? (k / (n - 1)) * 100 : 0) + '% 0';
  });
  const probe = new Image();
  probe.onload = () => { for (const s of $('filterGrid').querySelectorAll('.filterthumb')) s.classList.remove('loading'); };
  probe.onerror = () => toast('Não foi possível gerar os exemplos de filtro.', true);
  probe.src = url;
  applyFilter();
}

$('filterGrid').addEventListener('click', (e) => {
  const b = e.target.closest('[data-filter]');
  if (!b) return;
  state.filter = b.dataset.filter;
  store.set('filter', state.filter);
  applyFilter();
});
$('filterRefresh').onclick = () => loadFilterSamples(true);

// Estilo da legenda: lembrado entre sessões; com um plano de edição em uso, o plano decide.
$('subtitleStyle').value = store.get('subtitleStyle') === 'classica' ? 'classica' : 'dinamica';
$('subtitleStyle').addEventListener('change', () => store.set('subtitleStyle', $('subtitleStyle').value));
$('refineSubtitles').checked = store.get('refineSubtitles') !== 'off';
$('refineSubtitles').addEventListener('change', () => store.set('refineSubtitles',
  $('refineSubtitles').checked ? 'on' : 'off'));
function syncSubtitleStyle() {
  const plan = current()?.edit_plans?.[exportPlanName()];
  const planned = Boolean(plan);
  if (plan) {
    $('subtitleStyle').value = plan.settings.subtitle_style;
    $('framing').value = plan.settings.framing;
    $('normalize').checked = plan.settings.normalize_audio;
  }
  $('framing').disabled = planned;
  $('normalize').disabled = planned;
  if (state.profiles) profileTouched();
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
    await monitorPreview(j.id, pid, cid);
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
    // Só envia para a fila: o painel acompanha cada exportação, e outros cortes podem ser enfileirados já.
    const j = await api('/api/projects/' + pid + '/clips/' + cid + '/export', 'POST', exportOptions(format, rights));
    toast(j.duplicate ? 'Este corte já está na fila de exportação.' : clipLabel(cid) + ' entrou na fila (' + format + ').');
    setStatus('Exportação na fila', 'busy');
    if (state.project?.id === pid) {
      state.project.jobs = [j, ...(state.project.jobs || []).filter((x) => x.id !== j.id)];
      renderExports();
    }
  } catch (e) {
    setStatus('Exportação não enviada', 'error');
    toast(e.message, true);
  } finally {
    state.exporting = false;
    updateControls();
  }
}

// ------------------------------------------------------------------ fila de exportações

function clipLabel(id) {
  const i = state.project?.clips.findIndex((c) => c.id === id) ?? -1;
  return i < 0 ? 'Corte removido' : '#' + (i + 1) + ' ' + state.project.clips[i].title;
}

function jobLine(j) {
  const kind = j.kind === 'narration' ? 'Narrado · ' : '';
  return kind + (j.format || '') + ' · ' + (j.message || 'Processando') + ' · ' + Math.round((j.progress || 0) * 100) + '%';
}

// Três escopos: o que está renderizando agora, o que espera a vez e o que já pode ser baixado.
function renderExports() {
  const p = state.project;
  const box = $('exportQueue');
  if (!p) return box.replaceChildren();
  const jobs = (p.jobs || []).filter((j) => !j.preview);
  const running = jobs.filter((j) => j.status === 'running' || j.status === 'cancelling');
  const queued = jobs.filter((j) => j.status === 'queued').sort((a, b) => a.created - b.created);
  const done = p.clips.flatMap((c) => Object.keys(c.exports || {}).map((format) => ({c, format})));
  // Falhas só enquanto não houver uma tentativa mais nova do mesmo corte e formato.
  const latest = new Map();
  for (const j of jobs) {
    const key = j.kind + j.clip_id + j.format;
    if (!latest.has(key) || latest.get(key).created < j.created) latest.set(key, j);
  }
  const failed = [...latest.values()].filter((j) => j.status === 'failed' || j.status === 'interrupted').slice(0, 5);

  const row = (title, sub, {progress = null, actions = '', tone = ''} = {}) =>
    '<li class="qrow ' + tone + '"><div class="qmain"><strong>' + escape(title) + '</strong><small>' + escape(sub) +
    '</small>' + (progress === null ? '' : '<progress max="100" value="' + Math.round(progress * 100) + '"></progress>') +
    '</div>' + actions + '</li>';
  const cancel = (j) => j.status === 'cancelling' ? '' :
    '<button type="button" class="btn ghost danger qcancel" data-cancel="' + j.id + '" title="Cancelar esta exportação">' +
    '<svg class="ic"><use href="#i-x"/></svg><span>Cancelar</span></button>';
  // A escolha de abrir/fechar um grupo vale até trocar de projeto; sem escolha, usa o padrão do grupo.
  const isOpen = (name, open) => name in state.queueClosed ? !state.queueClosed[name] : open;
  const group = (name, label, items, body, open = true) => items.length
    ? '<details class="qgroup" data-group="' + name + '"' + (isOpen(name, open) ? ' open' : '') +
      '><summary>' + label + ' <b>' + items.length + '</b></summary><ul>' + body + '</ul></details>'
    : '';

  box.innerHTML =
    group('running', 'Em andamento', running, running.map((j) =>
      row(clipLabel(j.clip_id), batchPosition(j) + jobLine(j), {progress: j.progress || 0, actions: cancel(j)})).join('')) +
    group('queued', 'Na fila', queued, queued.map((j, i) =>
      row(clipLabel(j.clip_id), (i + 1) + 'º na fila · ' + (j.kind === 'narration' ? 'Narrado · ' : '') + (j.format || ''),
        {actions: cancel(j)})).join('')) +
    group('done', 'Concluídas', done, done.map(({c, format}) =>
      row(clipLabel(c.id), format + (c.exports[format]?.validation?.duration ? ' · ' + fmt(c.exports[format].validation.duration) : ''), {
        tone: 'ok',
        actions: '<span class="qactions"><button type="button" class="iconbtn" title="Assistir" aria-label="Assistir" ' +
          'data-watch="' + c.id + '" data-format="' + escape(format) + '"><svg class="ic"><use href="#i-eye"/></svg></button>' +
          '<a class="iconbtn" title="Baixar MP4" aria-label="Baixar MP4" href="' + downloadUrl(p.id, c.id, format) +
          '"><svg class="ic"><use href="#i-download"/></svg></a></span>',
      })).join(''), done.length <= 3) +
    group('failed', 'Com erro', failed, failed.map((j) =>
      row(clipLabel(j.clip_id), (j.format || '') + ' · ' + (j.error || 'A exportação não terminou.'), {
        tone: 'error',
        actions: j.kind === 'export' && !j.narration ? '<button type="button" class="btn ghost" data-retry="' + j.id +
          '">Tentar de novo</button>' : '',
      })).join(''));

  $('openFolder').hidden = !done.length;
  const batch = batchStatus(p);
  if (batch) {
    $('jobStatus').textContent = 'Lote: ' + batch.done + ' de ' + batch.total + ' prontos' +
      (batch.failed ? ' · ' + batch.failed + ' com erro' : '') + (batch.current ? ' · renderizando corte ' + batch.current : '');
    $('exportBar').hidden = false;
    $('exportBar').value = Math.round(100 * (batch.done + batch.failed + batch.progress) / batch.total);
    if (batch.finished) finishBatch(batch);
  } else if (state.exporting !== 'preview') {
    const parts = [[running, 'em andamento'], [queued, 'na fila'], [done, done.length === 1 ? 'concluída' : 'concluídas']]
      .filter(([items]) => items.length).map(([items, label]) => items.length + ' ' + label);
    $('jobStatus').textContent = parts.length ? parts.join(' · ') : EXPORT_IDLE;
  }
  if ((running.length || queued.length) && !state.queueTimer) state.queueTimer = setTimeout(pollExports, 1500);
}

const downloadUrl = (pid, cid, format) =>
  '/api/projects/' + pid + '/clips/' + cid + '/download?format=' + encodeURIComponent(format);

// Enquanto houver algo na fila, consulta só a lista de tarefas (leve); o projeto inteiro é recarregado
// apenas quando uma exportação termina, para os cortes mostrarem o arquivo novo.
async function pollExports() {
  state.queueTimer = null;
  const p = state.project;
  if (!p) return;
  let jobs;
  try { jobs = (await api('/api/projects/' + p.id + '/jobs')).jobs; } catch {
    // Servidor ainda sem a rota leve (não reiniciado desde a atualização): usa o projeto inteiro.
    try { jobs = (await api('/api/projects/' + p.id)).jobs.filter((j) => j.kind !== 'analysis'); } catch { jobs = null; }
  }
  if (state.project !== p) return;
  if (!jobs) { state.queueTimer = setTimeout(pollExports, 5000); return; }
  const wasActive = new Set((p.jobs || []).filter((j) => ACTIVE_JOB.includes(j.status)).map((j) => j.id));
  const finished = jobs.filter((j) => wasActive.has(j.id) && j.status === 'done' && !j.preview);
  p.jobs = [...jobs, ...(p.jobs || []).filter((j) => j.kind === 'analysis')];
  if (finished.length) {
    setStatus('Exportação concluída', 'ok');
    toast(finished.length === 1 ? clipLabel(finished[0].clip_id) + ' exportado (' + finished[0].format + ').'
      : finished.length + ' cortes exportados.');
    await refresh();
  } else {
    renderExports();
  }
}

$('exportQueue').addEventListener('click', async (e) => {
  const cancelBtn = e.target.closest('[data-cancel]');
  if (cancelBtn) {
    cancelBtn.disabled = true;
    try {
      await api('/api/jobs/' + cancelBtn.dataset.cancel + '/cancel', 'POST');
      toast('Exportação cancelada.');
      pollExports();
    } catch (err) { toast(err.message, true); cancelBtn.disabled = false; }
    return;
  }
  const watch = e.target.closest('[data-watch]');
  if (watch) {
    const c = state.project.clips.find((x) => String(x.id) === watch.dataset.watch);
    if (!c) return;
    const format = watch.dataset.format;
    openPreview(downloadUrl(state.project.id, c.id, format), c.exports?.[format], {preview: false});
  }
});
$('exportQueue').addEventListener('toggle', (e) => {
  const d = e.target.closest('details[data-group]');
  if (d) state.queueClosed = {...state.queueClosed, [d.dataset.group]: !d.open};
}, true);

// Só a prévia é acompanhada aqui: abre o vídeo ao terminar. As exportações vão para a fila do painel.
async function monitorPreview(id, pid, cid) {
  for (let i = 0; i < 2400; i++) {
    const j = await api('/api/jobs/' + id);
    const here = state.project?.id === pid;
    if (here && ACTIVE_JOB.includes(j.status)) {
      showExport((j.message || 'Processando') + ' · ' + Math.round((j.progress || 0) * 100) + '%',
        {progress: j.progress || 0, cancellable: j.status !== 'cancelling'});
    }
    if (j.status === 'done') {
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
    if (j.status === 'cancelled') {
      if (here) showExport('Prévia cancelada.', {tone: 'muted'});
      setStatus('Prévia cancelada', 'idle');
      return;
    }
    if (FAILED_JOB.includes(j.status)) throw Error(j.error || 'A prévia falhou.');
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
loadFilterCatalog();
updateControls();
const initial = new URLSearchParams(location.search).get('projeto');
if (!initial) setView('home');
if (initial) openProject(initial).catch((e) => { setLoading(null); setStatus('Projeto não encontrado', 'error'); toast(e.message, true); });
