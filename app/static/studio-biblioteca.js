'use strict';
// AutoCortes Studio — Biblioteca, importação, correção do texto da legenda, seleção múltipla, exportação em lote e pastas.
// Scripts clássicos carregados em ordem por studio.html: compartilham as variáveis globais do studio.js.

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
    (['failed', 'interrupted'].includes(j.status) && ['export', 'analysis', 'download'].includes(j.kind)
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
    escape(c.sources.map((o) => SOURCE_LABELS[o] || o).join(' + ')) + '</small></li>' : '');
  $('whyWarnings').innerHTML = warnings.map((w) => '<li>' + escape(w) + '</li>').join('');
}
const SOURCE_LABELS = {ia: 'IA', heuristica: 'heurística', publico: 'clipes do público na Twitch', chat: 'pico no chat'};
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

// ------------------------------------------------------------------ corrigir o texto da legenda

// Lista as frases do corte com o texto editável; clicar no tempo leva o vídeo até a frase. Só as frases
// alteradas são enviadas; a legenda precisa não desfaz as corrigidas.
state.subs = null;
$('editSubtitles').onclick = async () => {
  const c = current();
  if (!c || !state.project) return;
  try {
    const r = await api('/api/projects/' + state.project.id + '/clips/' + c.id + '/subtitles');
    state.subs = {clip: c.id, segments: r.segments};
    $('subsNote').textContent = (r.segments.length ? '' : 'Este corte ainda não tem fala transcrita. ') +
      (r.refined ? 'Texto da legenda precisa.' : 'Ainda é o texto da análise: na próxima prévia ou exportação a ' +
        'legenda precisa refaz as frases que você não corrigir.') + ' Apague o texto de uma frase para tirá-la da legenda.';
    $('subsList').innerHTML = r.segments.map((s, i) =>
      '<li><button type="button" class="subtime" data-subseek="' + i + '" title="Ir para este ponto">' +
      escape(fmt(Math.max(0, s.start - c.start))) + '</button><input data-sub="' + i + '" maxlength="400" ' +
      'aria-label="Texto da frase ' + (i + 1) + '" value="' + escape(s.text) + '">' +
      (s.edited ? '<small class="subedited">corrigida</small>' : '') + '</li>').join('');
    $('subsDialog').showModal();
  } catch (err) { toast(err.message, true); }
};
$('subsList').addEventListener('click', (e) => {
  const b = e.target.closest('[data-subseek]');
  if (b && state.subs) video.currentTime = state.subs.segments[Number(b.dataset.subseek)].start;
});
$('subsForm').onsubmit = async (e) => {
  e.preventDefault();
  const s = state.subs;
  if (!s || !state.project) return;
  const changed = [...$('subsList').querySelectorAll('[data-sub]')]
    .map((input) => ({seg: s.segments[Number(input.dataset.sub)], text: input.value.trim()}))
    .filter(({seg, text}) => text !== seg.text.trim())
    .map(({seg, text}) => ({start: seg.start, end: seg.end, text}));
  if (!changed.length) { $('subsDialog').close(); return; }
  try {
    const r = await api('/api/projects/' + state.project.id + '/clips/' + s.clip + '/subtitles', 'PUT', {segments: changed});
    $('subsDialog').close();
    toast(r.changed === 1 ? '1 frase corrigida. Vale na próxima prévia ou exportação.'
      : r.changed + ' frases corrigidas. Valem na próxima prévia ou exportação.');
  } catch (err) { toast(err.message, true); }
};

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
  if (rights && !$('batchRights').checked) return toast('Confirme os direitos autorais para exportar.', true);
  store.set('batch.presets', JSON.stringify(presets));
  // Aviso do sistema quando o lote terminar (pedido agora, no clique: o navegador só pergunta com um gesto).
  if (window.Notification?.permission === 'default') Notification.requestPermission().catch(() => {});
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
  if (document.hidden && window.Notification?.permission === 'granted') {
    try {
      new Notification('AutoCortes: lote concluído', {body: batch.done + ' de ' + batch.total + ' arquivos prontos' +
        (batch.failed ? ', ' + batch.failed + ' com erro.' : '.'), tag: 'autocortes-lote'});
    } catch { /* sem aviso do sistema: o aviso na tela continua */ }
  }
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
