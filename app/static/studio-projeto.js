'use strict';
// AutoCortes Studio — Projeto aberto: carga, enquadramento por trecho, área do streamer e escolha dos cortes.
// Scripts clássicos carregados em ordem por studio.html: compartilham as variáveis globais do studio.js.

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
  // Na atualização periódica da análise, mantém o andamento na tela até a resposta chegar (sem piscar).
  if (state.project?.id !== id) {
    setStatus('Carregando projeto…', 'busy');
    setLoading('Carregando projeto…');
  }
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
  // Análise parada depois da transcrição: a transcrição ficou gravada e a seleção pode ser refeita a partir dela.
  const transcribed = p.transcription?.state === 'ok';
  $('openSelection').hidden = !(transcribed && (p.status === 'ready' || STOPPED.includes(p.status)));
  $('reanalyze').hidden = !(p.status === 'ready' || STOPPED.includes(p.status));
  loadCacheUsage();
  render();
  loadProjects();
  closeDrawers();
  if (p.status === 'queued' || p.status === 'analyzing') {
    const a = analysisLabel(p.analysis, p.analysis_events || []);
    setStatus(a.pct === null ? 'Analisando vídeo…' : 'Analisando vídeo · ' + a.pct + '%', 'busy');
    setLoading(a.text, a.progress, a.steps);
    showTip('analyzing');
    setTimeout(() => {
      if (state.openVersion === version) openProject(id).catch((e) => toast(e.message, true));
    }, 1700);
  } else if (p.status === 'ready') {
    setStatus('Projeto aberto', 'ok');
    showTip(p.clips?.length ? 'review' : 'manual');
    if (filtersVisible()) loadFilterSamples();
    setLoading(video.readyState >= 2 ? null : 'Carregando vídeo…');
  } else {
    setLoading(null);
    showTip(null);
    setStatus('Projeto indisponível', 'error');
    toast((p.error || 'O projeto não está pronto para edição.') + (transcribed
      ? ' A transcrição ficou salva: use Escolha dos cortes com IA para continuar sem transcrever de novo.' : ''), true);
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
  showTip(null);
  syncCover();
  hideExport();
  $('noVideo').hidden = false;
  $('mediaCard').hidden = true;
  $('mediaEmpty').hidden = false;
  $('openRights').hidden = true;
  $('cutTools').hidden = true;
  $('openSelection').hidden = true;
  $('reanalyze').hidden = true;
  $('clearCache').hidden = true;
  $('projectName').textContent = $('crumb').textContent = 'Nenhum projeto aberto';
  $('projectMeta').textContent = 'Importe um vídeo ou abra um projeto recente';
  document.title = 'AutoCortes Studio';
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
      'falhar, eles continuam). Para só escolher os cortes de novo, sem transcrever, use “Escolha dos cortes com IA”.',
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

// Cache do projeto: só aparece com algo para liberar (1 MB ou mais).
const cacheSize = (b) => (b < 1e6 ? Math.max(1, Math.round(b / 1e3)) + ' KB' : fmtSize(b));
async function loadCacheUsage() {
  const id = state.project?.id;
  if (!id) return;
  try {
    const u = await api('/api/projects/' + id + '/cache');
    if (state.project?.id !== id) return;
    state.cacheUsage = u;
    $('clearCacheText').textContent = 'Liberar espaço (' + cacheSize(u.total_bytes) + ')';
    $('clearCache').hidden = u.total_bytes < 1e6;
  } catch { $('clearCache').hidden = true; }
}
$('clearCache').onclick = async () => {
  const id = state.project?.id, u = state.cacheUsage;
  if (!id || !u) return;
  const parts = u.parts.filter((x) => x.bytes).map((x) => x.label.toLowerCase() + ' (' + cacheSize(x.bytes) + ')');
  const ok = await askConfirm({title: 'Liberar espaço', yes: 'Liberar ' + cacheSize(u.total_bytes),
    text: 'Apaga ' + parts.join(', ') + '. Tudo é refeito quando necessário (a próxima prévia ou exportação ' +
      'pode demorar um pouco mais). O vídeo, a transcrição, os cortes e as exportações não são apagados.'});
  if (!ok) return;
  try {
    const r = await api('/api/projects/' + id + '/cache', 'DELETE');
    toast('Espaço liberado: ' + cacheSize(r.freed_bytes) + '.');
    for (const c of state.project?.clips || []) delete c.preview;
    state.cacheUsage = r;
    $('clearCache').hidden = true;
  } catch (err) { toast(err.message, true); }
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
  if (c.chat_peak) out.push('Pico no chat');
  if (c.community) out.push('Clipado ' + c.community.count + '× · ' + c.community.views.toLocaleString('pt-BR') + ' views');
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
  syncFocus();
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
  const segs = mixSegments(c);
  // Início e fim editáveis (o início do primeiro e o fim do último são os do corte); mudar um limite move
  // o do trecho vizinho junto. Focar um campo leva o vídeo até aquele ponto.
  const time = (i, which, value, fixed) => '<input class="t" inputmode="decimal" spellcheck="false" autocomplete="off"' +
    ' data-segtime="' + i + '" data-which="' + which + '" value="' + fmt(value) + '"' + (fixed ? ' readonly tabindex="-1"' : '') +
    ' aria-label="' + (which === 'start' ? 'Início' : 'Fim') + ' do trecho ' + (i + 1) + '" title="' +
    (fixed ? (which === 'start' ? 'Começo do corte' : 'Fim do corte') : 'Digite o tempo (ex.: 0:52.5) e tecle Enter') + '">';
  $('mixSegments').innerHTML = segs.map((s, i) =>
    '<li' + (at >= s.start && at < s.end ? ' class="here"' : '') + '>' +
    '<span class="segtimes">' + time(i, 'start', s.start, i === 0) + '<span aria-hidden="true">–</span>' +
    time(i, 'end', s.end, i === segs.length - 1) + '</span>' +
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
  const del = e.target.closest('[data-del]');
  if (del) saveMix(mixSegments(c).filter((_, i) => i !== Number(del.dataset.del)));
};
$('mixSegments').addEventListener('focusin', (e) => {
  const input = e.target.closest('[data-segtime]');
  const c = current();
  if (input && c && !Number.isNaN(parseTime(input.value))) video.currentTime = c.start + parseTime(input.value);
});
$('mixSegments').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && e.target.closest('[data-segtime]')) { e.preventDefault(); e.target.blur(); }
  if (e.key === 'Escape' && e.target.closest('[data-segtime]')) { e.preventDefault(); renderMix(); }
});
// Novo limite entre dois trechos: cada trecho fica com pelo menos MIX_MIN segundos.
const MIX_MIN = 0.5;
function moveBoundary(i, which, text) {
  const c = current();
  if (!c) return;
  const segs = mixSegments(c);
  const t = parseTime(text);
  const k = which === 'end' ? i : i - 1;     // limite entre os trechos k e k + 1
  if (Number.isNaN(t)) { toast('Tempo inválido. Use, por exemplo, 52.5 ou 0:52.5.', true); return renderMix(); }
  if (k < 0 || k >= segs.length - 1) return renderMix();
  const lo = segs[k].start + MIX_MIN, hi = segs[k + 1].end - MIX_MIN;
  const v = Math.round(Math.min(hi, Math.max(lo, t)) * 100) / 100;
  if (Math.abs(v - t) > 0.01) toast('Ajustado para ' + fmt(v) + ': cada trecho precisa de pelo menos ' + MIX_MIN + ' s entre os vizinhos.');
  if (Math.abs(v - segs[k].end) < 0.005) return renderMix();
  segs[k].end = segs[k + 1].start = v;
  saveMix(segs);
}
$('mixSegments').onchange = (e) => {
  const input = e.target.closest('[data-segtime]');
  if (input) return moveBoundary(Number(input.dataset.segtime), input.dataset.which, input.value);
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

// ------------------------------------------------------------------ área do streamer desenhada à mão

// Retângulo em fração do quadro inteiro do vídeo. O player encaixa o vídeo na caixa (object-fit: contain),
// então as contas usam a área onde a imagem aparece de fato.
state.focusEdit = null;
function videoArea() {
  const box = $('videoBox').getBoundingClientRect();
  const vw = video.videoWidth || 16, vh = video.videoHeight || 9;
  const s = Math.min(box.width / vw, box.height / vh);
  const w = vw * s, h = vh * s;
  return {left: (box.width - w) / 2, top: (box.height - h) / 2, width: w, height: h, box};
}
function placeFocus(rect, el = $('focusArea')) {
  const a = videoArea();
  el.hidden = !rect;
  if (!rect) return;
  el.style.left = (a.left + rect[0] * a.width) + 'px';
  el.style.top = (a.top + rect[1] * a.height) + 'px';
  el.style.width = (rect[2] * a.width) + 'px';
  el.style.height = (rect[3] * a.height) + 'px';
}
// Legenda já gravada no vídeo: uma área por projeto, apagada em todas as exportações.
function syncCover() {
  const rect = state.project?.caption_cover || null;
  const editing = state.focusEdit?.kind === 'cover';
  $('coverClear').hidden = !rect;
  $('coverDraw').querySelector('span').textContent = rect ? 'Marcar de novo' : 'Marcar legenda antiga';
  $('coverInfo').textContent = rect
    ? 'A área marcada (desfocada no player) é apagada em todos os cortes deste projeto, e a legenda nova entra no lugar.'
    : 'Se o vídeo já tem legenda na imagem, marque onde ela fica para apagá-la na exportação.';
  placeFocus(editing || !video.videoWidth ? null : rect, $('coverMark'));
}

function syncFocus() {
  const c = current();
  const usable = Boolean(c) && ['streamer', 'split', 'mix'].includes($('framing').value);
  $('focusBox').hidden = !usable;
  syncCover();
  if (state.focusEdit) return;   // desenhando: a camada fica como está
  const rect = usable ? c.focus_rect : null;
  $('focusClear').hidden = !rect;
  $('focusInfo').textContent = rect
    ? 'Área definida à mão (contorno tracejado no vídeo): o "Só o streamer" mostra só essa parte neste corte.'
    : 'A área do streamer é achada sozinha (janela da webcam ou da bancada). Se errar, desenhe a área no vídeo.';
  $('focusLayer').hidden = !rect;
  $('focusLayer').classList.remove('editing');
  $('focusBar').hidden = true;
  placeFocus(rect);
}
$('framing').addEventListener('change', syncFocus);
video.addEventListener('loadedmetadata', syncFocus);
window.addEventListener('resize', () => (state.focusEdit ? placeFocus(state.focusEdit.rect) : syncFocus()));

const DRAW_HELP = {focus: 'Arraste sobre o vídeo para marcar a área do streamer.',
  cover: 'Pare num trecho com a legenda e arraste sobre ela, com uma pequena folga.'};
function startDraw(kind, rect) {
  if (!video.paused) video.pause();
  state.focusEdit = {kind, rect, from: null};
  $('focusHelp').textContent = DRAW_HELP[kind];
  syncCover();
  $('focusLayer').hidden = false;
  $('focusLayer').classList.add('editing');
  $('focusBar').hidden = false;
  $('focusOk').disabled = !state.focusEdit.rect;
  placeFocus(state.focusEdit.rect);
}
$('focusDraw').onclick = () => current() && startDraw('focus', current().focus_rect || null);
$('coverDraw').onclick = () => {
  if (!state.project || !video.videoWidth) return toast('Abra um projeto com o vídeo carregado para marcar a legenda.', true);
  startDraw('cover', state.project.caption_cover || null);
};
function focusPoint(e) {
  const a = videoArea();
  return [Math.min(1, Math.max(0, (e.clientX - a.box.left - a.left) / a.width)),
    Math.min(1, Math.max(0, (e.clientY - a.box.top - a.top) / a.height))];
}
$('focusLayer').addEventListener('pointerdown', (e) => {
  if (!state.focusEdit || e.target.closest('#focusBar')) return;
  e.preventDefault();
  $('focusLayer').setPointerCapture(e.pointerId);
  state.focusEdit.from = focusPoint(e);
});
$('focusLayer').addEventListener('pointermove', (e) => {
  const f = state.focusEdit;
  if (!f?.from) return;
  const [x, y] = focusPoint(e);
  f.rect = [Math.min(x, f.from[0]), Math.min(y, f.from[1]), Math.abs(x - f.from[0]), Math.abs(y - f.from[1])];
  placeFocus(f.rect);
});
$('focusLayer').addEventListener('pointerup', () => {
  const f = state.focusEdit;
  if (!f?.from) return;
  f.from = null;
  const min = f.kind === 'cover' ? 0.02 : 0.05;   // a legenda antiga é uma faixa baixa
  const big = f.rect && f.rect[2] >= min && f.rect[3] >= min;
  $('focusOk').disabled = !big;
  $('focusHelp').textContent = big ? 'Confira a área. Arraste de novo para refazer.' : 'Área pequena demais: arraste de novo.';
});
function endFocusEdit() {
  state.focusEdit = null;
  syncFocus();
}
async function saveCover(rect) {
  if (!state.project) return;
  try {
    const r = await api('/api/projects/' + state.project.id + '/caption-cover', 'PUT', {rect});
    state.project.caption_cover = r.caption_cover || undefined;
    toast(rect ? 'A legenda antiga será apagada na exportação de todos os cortes.' : 'A legenda antiga não será mais apagada.');
  } catch (err) { toast(err.message, true); }
  endFocusEdit();
}
$('focusCancel').onclick = endFocusEdit;
async function saveFocus(rect) {
  const c = current();
  if (!c || !state.project) return;
  try {
    const r = await api('/api/projects/' + state.project.id + '/clips/' + c.id + '/focus', 'PUT', {rect});
    c.focus_rect = r.focus_rect || undefined;
    toast(rect ? 'Área do streamer definida para este corte.' : 'Voltou para a área automática.');
  } catch (err) { toast(err.message, true); }
  endFocusEdit();
}
$('focusOk').onclick = () => {
  const f = state.focusEdit;
  if (!f?.rect) return;
  const rect = f.rect.map((v) => Math.round(v * 10000) / 10000);
  return f.kind === 'cover' ? saveCover(rect) : saveFocus(rect);
};
$('focusClear').onclick = () => saveFocus(null);
$('coverClear').onclick = () => saveCover(null);
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && state.focusEdit) { e.preventDefault(); endFocusEdit(); } });

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
    // Só as duas IAs aparecem; a heurística roda sozinha quando a IA escolhida não está disponível.
    const chosen = ['ollama', 'gemini'].includes(cur.provider) ? cur.provider : 'ollama';
    $('curationProvider').innerHTML = Object.entries(providers).filter(([id]) => id !== 'heuristica').map(([id, info]) =>
      '<option value="' + id + '"' + (id === chosen ? ' selected' : '') + '>' + escape(info.label) +
      (!info.configured ? (id === 'ollama' ? ' (não instalada)' : ' (sem chave)') : '') + '</option>').join('');
    $('curationProvider').dataset.providers = JSON.stringify(providers);
    $('curationConsent').checked = !!cur.consent;
    $('curationAllClips').checked = !!cur.all_clips;
    $('curationPrompt').value = cur.prompt || '';
    syncCuration();
    const vod = state.project?.twitch_vod;
    $('twitchVod').value = vod ? 'https://www.twitch.tv/videos/' + vod : '';
    $('twitchVod').disabled = !state.project;
    $('projectChannel').value = state.project?.channel || state.project?.community?.video?.channel || '';
    $('projectChannel').disabled = !state.project;
    const pref = state.project?.selection?.preference;
    $('preferenceNote').textContent = pref
      ? 'Na última escolha: ' + pref.good + ' 👍 e ' + pref.bad + ' 👎 ' + (pref.scope === 'canal' ? 'deste canal' : 'de todos os projetos') + ' ajustaram a nota.'
      : 'Ainda não há avaliações suficientes: são precisos pelo menos 3 👍 e 3 👎.';
    $('preferenceNote').hidden = !state.project;
    syncTwitch(health.twitch || {});
    syncChat();
    const clips = state.project?.clips || [];
    const exported = clips.filter((c) => Object.keys(c.exports || {}).length).length;
    $('reselectNote').textContent = 'Escolher de novo usa a transcrição salva, sem transcrever outra vez' + (clips.length
      ? ' e substitui os ' + clips.length + ' cortes atuais' + (exported ? ', incluindo ' + exported + ' já exportado(s) — baixe-os antes' : '')
      : '') + '.';
    dlg.showModal();
  } catch (e) { toast(e.message, true); }
}

function syncCuration() {
  const id = $('curationProvider').value;
  const info = JSON.parse($('curationProvider').dataset.providers || '{}')[id] || {};
  // Ollama: nada sai do computador, então não há consentimento nem chave; mostra o status do modelo.
  $('curationConsentRow').hidden = !!info.local;
  $('curationKeyRow').hidden = id !== 'gemini';
  $('curationKeyRemove').hidden = info.key_source !== 'saved';
  const fallback = ' Enquanto isso, os cortes são escolhidos sem IA.';
  const source = info.key_source === 'saved' ? 'Chave salva (termina em …' + info.key_hint + ')'
    : 'Chave da variável GEMINI_API_KEY';
  $('curationKeyNote').textContent = id === 'ollama'
    ? 'Roda neste computador: nada é enviado para a internet. ' + (info.message || '') + (info.configured ? '' : fallback)
    : info.configured ? source + '. Modelo: ' + (info.model || '—') + '.'
      : 'Sem chave: cole a chave do Gemini abaixo.' + fallback;
  $('curationKeyNote').classList.toggle('warn', !info.configured);
  $('curationKeyNote').hidden = false;
}

// Atualiza o status do Gemini na lista depois de salvar ou remover a chave.
function applyKeyStatus(info) {
  const select = $('curationProvider');
  const providers = JSON.parse(select.dataset.providers || '{}');
  providers.gemini = {...providers.gemini, ...info};
  select.dataset.providers = JSON.stringify(providers);
  const option = [...select.options].find((o) => o.value === 'gemini');
  if (option) option.textContent = info.label + (info.configured ? '' : ' (sem chave)');
  $('curationKey').value = '';
  syncCuration();
}
$('curationKeyRemove').onclick = async () => {
  try { applyKeyStatus(await api('/api/settings/keys/gemini', 'DELETE')); toast('Chave do Gemini removida.'); }
  catch (e) { toast(e.message, true); }
};
$('curationProvider').addEventListener('change', syncCuration);

function syncTwitch(info) {
  $('twitchId').value = '';
  $('twitchSecret').value = '';
  $('twitchKeyRemove').hidden = info.key_source !== 'saved';
  const last = state.project?.community;
  const parts = [info.configured
    ? (info.key_source === 'saved' ? 'Credenciais salvas (Client ID termina em …' + info.key_hint + ').'
      : 'Credenciais das variáveis TWITCH_CLIENT_ID e TWITCH_CLIENT_SECRET.')
    : 'Sem credenciais: cole o Client ID e o Client Secret abaixo.'];
  if (!info.aligner) parts.push('Sem o yt-dlp, o app usa o ponto que a Twitch informa, e só quando este vídeo é a live inteira.');
  if (last?.message && state.project?.twitch_vod) parts.push('Última busca: ' + last.message);
  $('twitchNote').textContent = parts.join(' ');
  $('twitchNote').classList.toggle('warn', !info.configured || last?.state === 'failed');
  $('twitchNote').hidden = false;
}
function syncChat() {
  const c = state.project?.chat;
  $('chatFile').value = '';
  $('chatFile').disabled = !state.project;
  $('chatRemove').hidden = !c;
  if (c) $('chatNote').textContent = 'Chat importado' + (c.name ? ' (' + c.name + ')' : '') + ': ' + c.message;
}
$('chatFile').addEventListener('change', async () => {
  const f = $('chatFile').files[0], p = state.project;
  if (!f || !p) return;
  try {
    const r = await fetch('/api/projects/' + p.id + '/chat', {method: 'PUT', body: f,
      headers: {...authHeaders(), ...CSRF, 'X-Filename': encodeURIComponent(f.name)}});
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw Error(data.message || 'Não foi possível importar o chat.');
    p.chat = data.chat;
    toast('Chat importado: ' + data.chat.messages.toLocaleString('pt-BR') + ' mensagens.');
  } catch (e) { toast(e.message, true); }
  syncChat();
});
$('chatRemove').onclick = async () => {
  const p = state.project;
  if (!p) return;
  try {
    const r = await fetch('/api/projects/' + p.id + '/chat', {method: 'PUT', headers: {...authHeaders(), ...CSRF}});
    if (!r.ok) throw Error('Não foi possível remover o chat.');
    p.chat = null;
    syncChat();
    toast('Chat removido do projeto.');
  } catch (e) { toast(e.message, true); }
};
$('twitchKeyRemove').onclick = async () => {
  try {
    await api('/api/settings/keys/twitch_id', 'DELETE');
    syncTwitch(await api('/api/settings/keys/twitch_secret', 'DELETE'));
    toast('Credenciais da Twitch removidas.');
  } catch (e) { toast(e.message, true); }
};

async function saveCuration() {
  const provider = $('curationProvider').value;
  const key = $('curationKey').value.trim();
  if (key) applyKeyStatus(await api('/api/settings/keys/gemini', 'PUT', {key}));
  const twitchId = $('twitchId').value.trim(), twitchSecret = $('twitchSecret').value.trim();
  if (twitchId) await api('/api/settings/keys/twitch_id', 'PUT', {key: twitchId});
  if (twitchSecret) syncTwitch(await api('/api/settings/keys/twitch_secret', 'PUT', {key: twitchSecret}));
  if (state.project) {
    const saved = await api('/api/projects/' + state.project.id + '/reference', 'PUT',
      {twitch_url: $('twitchVod').value.trim(), channel: $('projectChannel').value.trim()});
    state.project.twitch_vod = saved.twitch_vod;
    state.project.channel = saved.channel;
  }
  await api('/api/settings', 'PUT', {curation: {
    provider, consent: provider === 'gemini' && $('curationConsent').checked,
    prompt: $('curationPrompt').value.trim(),
    all_clips: $('curationAllClips').checked,
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
    toast('Escolhendo os cortes de novo com a transcrição salva…');
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
