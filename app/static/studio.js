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

// ------------------------------------------------------------------ tema
// Automático segue o sistema; claro e escuro ficam salvos neste navegador. O CSS já aplica o tema do sistema
// antes deste script, então só a escolha explícita precisa de data-theme.
const THEMES = {auto: 'automático', light: 'claro', dark: 'escuro'};
const NEXT_THEME = {auto: 'light', light: 'dark', dark: 'auto'};
function applyTheme(choice) {
  if (!THEMES[choice]) choice = 'auto';
  if (choice === 'auto') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = choice;
  const btn = $('themeToggle');
  btn.dataset.choice = choice;
  btn.setAttribute('aria-label', 'Tema ' + THEMES[choice] + '. Trocar tema');
  btn.title = 'Tema ' + THEMES[choice] + '. Clique para trocar.';
  requestAnimationFrame(() => drawWave());   // a onda do áudio é desenhada com a cor do tema
}
applyTheme(store.get('studio.theme'));
$('themeToggle').onclick = () => {
  const next = NEXT_THEME[$('themeToggle').dataset.choice] || 'auto';
  store.set('studio.theme', next);
  applyTheme(next);
};
window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => drawWave());

// ------------------------------------------------------------------ modo simples e avançado
// O simples (padrão) mostra o essencial: cortar, legendas, enquadramento e exportar. O avançado mostra filtros,
// estilos de edição, perfis, zoom da linha do tempo e automação. Só esconde: nenhuma opção já escolhida muda.
function setMode(mode, persist = true) {
  mode = mode === 'advanced' ? 'advanced' : 'simple';
  document.body.dataset.mode = mode;
  const btn = $('modeToggle');
  btn.setAttribute('aria-pressed', String(mode === 'advanced'));
  btn.title = mode === 'advanced' ? 'Esconder as ferramentas avançadas' : 'Mostrar todas as ferramentas do editor';
  if (persist) store.set('studio.mode', mode);
}
setMode(store.get('studio.mode'), false);
$('modeToggle').onclick = () => {
  const next = document.body.dataset.mode === 'advanced' ? 'simple' : 'advanced';
  setMode(next);
  toast(next === 'advanced' ? 'Modo avançado: todas as ferramentas à mostra.' : 'Modo simples: só o essencial à mostra.');
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
const STAGE_NAMES = {validando: 'Validação', extraindo_audio: 'Áudio', transcrevendo: 'Transcrição',
  cenas: 'Cenas', audio_eventos: 'Reações no áudio', selecionando: 'Seleção', ia_local: 'IA neste computador', curadoria_ia: 'Escolha com IA', clipes_publico: 'Clipes da Twitch', chat: 'Chat da live', fila: 'Fila'};
// Faixa da barra geral ocupada pelas etapas com progresso real (as outras são rápidas ou sem medida).
const STAGE_SPANS = {extraindo_audio: [0.08, 0.14], transcrevendo: [0.14, 0.74], cenas: [0.75, 0.88]};
const fmtDur = (s) => {
  s = Math.max(0, Math.round(s));
  if (s < 60) return s + ' s';
  if (s < 3600) return Math.floor(s / 60) + ' min' + (s < 600 && s % 60 ? ' ' + (s % 60) + ' s' : '');
  return Math.floor(s / 3600) + ' h ' + Math.floor((s % 3600) / 60) + ' min';
};
const etaStart = {key: null, ts: 0, done: 0};   // primeiro avanço visto na etapa atual
function analysisLabel(job, events = []) {
  if (!job) return {text: 'Analisando vídeo…', progress: null, pct: null, steps: ''};
  const now = Date.now() / 1000;
  const pct = Math.round((job.progress || 0) * 100);
  const stage = job.status === 'queued' ? 'Na fila' : (job.message || 'Analisando vídeo');
  // Tempo restante da etapa atual pela velocidade dela até agora (transcrição, cenas, extração de áudio).
  // A velocidade conta a partir do primeiro avanço visto: o carregamento do modelo antes dele não entra na conta.
  let eta = null;
  const span = STAGE_SPANS[job.stage];
  const begun = [...events].reverse().find((e) => e.stage === job.stage)?.ts;
  if (span && begun) {
    const done = ((job.progress || 0) - span[0]) / (span[1] - span[0]);
    const key = job.id + ':' + job.stage;
    if (done > 0 && etaStart.key !== key) Object.assign(etaStart, {key, ts: now, done});
    if (etaStart.key === key && done - etaStart.done > 0.01 && now - etaStart.ts > 20) {
      eta = (now - etaStart.ts) * (1 - done) / (done - etaStart.done);
    } else if (done > 0.03 && now - begun > 20) {
      eta = (now - begun) * (1 - done) / done;   // página aberta no meio da etapa: média desde o início dela
    }
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

// Credentials stay in memory; closing/reloading the page clears them.
let sessionAuthorization = '';
let authenticationRequest = null;
function authHeaders() { return sessionAuthorization ? {Authorization: sessionAuthorization} : {}; }
function requestAuthentication() {
  if (authenticationRequest) return authenticationRequest;
  authenticationRequest = new Promise((resolve) => {
    const dialog = $('authDialog'), input = $('authPassword');
    input.value = '';
    dialog.returnValue = '';
    dialog.addEventListener('close', () => {
      const password = input.value;
      input.value = '';
      const accepted = dialog.returnValue === 'login' && Boolean(password);
      if (accepted) {
        const bytes = new TextEncoder().encode('autocortes:' + password);
        sessionAuthorization = 'Basic ' + btoa(Array.from(bytes, b => String.fromCharCode(b)).join(''));
      }
      authenticationRequest = null;
      resolve(accepted);
    }, {once: true});
    dialog.showModal();
    input.focus();
  });
  return authenticationRequest;
}
function responseError(status, data = {}) {
  const code = status === 413 ? 'upload_too_large' : data.code;
  const message = status === 413
    ? 'O servidor ou túnel recusou o tamanho do vídeo. Use um arquivo menor ou abra o AutoCortes no computador onde o vídeo está salvo e escolha “Do computador”.'
    : status === 401 && code === 'auth_required'
      ? 'Acesso não autorizado. Informe a senha configurada no servidor e tente novamente.'
      : typeof data.detail === 'string' ? data.detail : 'Erro ao executar a operação.';
  const error = Error(withHint(message, code));
  error.code = code;
  return error;
}

async function api(url, method = 'GET', body, authRetried = false) {
  let r;
  try {
    r = await fetch(url, {
      method,
      headers: {...authHeaders(), ...(method === 'GET' ? {} : CSRF), ...(body ? {'Content-Type': 'application/json'} : {})},
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw Error('Sem conexão com o AutoCortes. Verifique se o aplicativo ainda está aberto.');
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    if (r.status === 401 && data.code === 'auth_required') {
      sessionAuthorization = '';
      if (!authRetried && await requestAuthentication()) return api(url, method, body, true);
    }
    throw responseError(r.status, data);
  }
  return data;
}

// O que fazer quando algo dá errado: a mensagem do servidor diz o problema; aqui entra a saída.
const ERROR_HINTS = {
  too_large: 'Use um arquivo menor ou aumente MAX_UPLOAD_GB no iniciar_windows.bat.',
  too_long: 'Corte o vídeo em partes ou aumente AUTOCORTES_MAX_SOURCE_HOURS no iniciar_windows.bat.',
  bad_extension: 'Converta para MP4 (H.264) e tente de novo.',
  no_audio: 'Sem áudio não há fala para analisar: use o modo Manual.',
  busy: 'Espere a tarefa atual terminar ou cancele em Tarefas.',
  not_ready: 'Aguarde a análise terminar; o andamento aparece na página inicial.',
  no_source: 'O vídeo original sumiu da pasta do projeto: crie o projeto de novo com o arquivo.',
  no_transcript: 'Use "Analisar de novo" para transcrever o vídeo.',
  disk_full: 'Libere espaço no disco e tente de novo.',
  rights_ack_required: 'Abra Direitos autorais, leia os alertas e confirme na exportação.',
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
    ? 'Seleção de ' + fmt(a) + ' a ' + fmt(b) + ' (' + fmt(b - a) + ')'
    : 'Marque a entrada (I) e a saída (O).';
  const band = $('selectionBand');
  band.hidden = !state.project || b - a <= 0;
  band.style.left = a * state.scale + 'px';
  band.style.width = (b - a) * state.scale + 'px';
}

function updateControls() {
  const ready = isReady(), c = current(), hasVideo = !!state.project;
  for (const id of ['back', 'play', 'forward', 'seek', 'markIn', 'markOut', 'mute', 'fullscreen']) $(id).disabled = !hasVideo;
  for (const b of document.querySelectorAll('[data-jump]')) b.disabled = !hasVideo;
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
    $('editorHeading').textContent = 'Editor: ' + state.project.name;
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
  // Declaradas nos arquivos seguintes do Studio: chamadas só no clique, quando todos já carregaram.
  jobs: () => openJobs(),
  automation: () => openAutomation(),
  settings: () => openSettings(),
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

// ------------------------------------------------------------------ guia de primeiro uso e dicas
// O guia abre sozinho só na primeira visita; depois fica em Ajuda. Cada dica some de vez quando é fechada.
let guideStep = 0;
function showGuideStep(i) {
  const steps = [...$('guideSteps').children];
  guideStep = clamp(i, 0, steps.length - 1);
  steps.forEach((s, k) => { s.hidden = k !== guideStep; });
  $('guideStepLabel').textContent = 'Passo ' + (guideStep + 1) + ' de ' + steps.length;
  $('guideBack').hidden = guideStep === 0;
  $('guideNext').textContent = guideStep === steps.length - 1 ? 'Começar' : 'Próximo';
}
function openGuide() {
  showGuideStep(0);
  $('guideDialog').showModal();
  $('guideNext').focus();
}
$('guideBack').onclick = () => showGuideStep(guideStep - 1);
$('guideNext').onclick = () => {
  if (guideStep === $('guideSteps').children.length - 1) $('guideDialog').close();
  else showGuideStep(guideStep + 1);
};
$('guideDialog').addEventListener('close', () => store.set('studio.guideSeen', '1'));
$('openGuide').onclick = () => { $('helpDialog').close(); openGuide(); };
if (!store.get('studio.guideSeen')) setTimeout(openGuide, 400);

const TIPS = {
  analyzing: 'A análise continua mesmo se você fechar esta aba, desde que o AutoCortes siga aberto. ' +
    'O andamento também aparece em Tarefas.',
  review: 'Estes são os cortes sugeridos. Clique em um para assistir, ajuste o início e o fim se precisar ' +
    'e clique em Exportar.',
  manual: 'Toque o vídeo, marque o início (I) e o fim (O) e clique em Cortar para criar um corte.',
};
function showTip(key) {
  const fresh = key && !store.get('studio.tip.' + key);
  $('tipBar').hidden = !fresh;
  if (!fresh) return;
  $('tipBar').dataset.key = key;
  $('tipText').textContent = TIPS[key];
}
$('tipClose').onclick = () => {
  store.set('studio.tip.' + $('tipBar').dataset.key, '1');
  $('tipBar').hidden = true;
};
narrow.addEventListener('change', () => { if (!narrow.matches) document.body.dataset.left = 'closed'; syncScrim(); });
phone.addEventListener('change', syncScrim);
