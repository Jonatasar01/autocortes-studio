'use strict';
// AutoCortes Studio — Perfis e modelos de visual, plano de edição, exportação, filtro e aparência da legenda, fila.
// Scripts clássicos carregados em ordem por studio.html: compartilham as variáveis globais do studio.js.

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
    filter: state.filter === 'nenhum' ? null : state.filter,
    subtitle_look: lookIsDefault(subtitleLook()) ? null : subtitleLook()};
}
const PROFILE_FIELDS = ['format', 'framing', 'subtitles', 'subtitle_style', 'refine_subtitles', 'normalize_audio',
  'hook', 'filter', 'subtitle_look'];
const sameLook = (a, b) => JSON.stringify({...LOOK_DEFAULT, ...(a || {})}).toLowerCase() ===
  JSON.stringify({...LOOK_DEFAULT, ...(b || {})}).toLowerCase();
const sameOptions = (p) => {
  const o = currentOptions();
  return PROFILE_FIELDS.every((k) => k === 'subtitle_look' ? sameLook(p[k], o[k]) : (p[k] ?? null) === o[k]);
};

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
  let key = state.project && store.get('profile.' + state.project.id);
  // Projeto novo de um canal com modelo de visual próprio: o modelo vale sem precisar escolher.
  const channel = (state.project?.channel || state.project?.community?.video?.channel || '').trim().toLowerCase();
  if (state.project && key === null && channel) {
    const own = state.profiles.saved.find((p) => (p.channel || '').trim().toLowerCase() === channel);
    if (own) key = profileKey(own);
  }
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
    if (p.subtitle_look !== undefined) {   // perfis antigos não têm aparência: a atual continua
      setLook(p.subtitle_look || LOOK_DEFAULT);
      store.set('subtitleLook', JSON.stringify(subtitleLook()));
    }
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
const FRAMING_NAMES = {fit: 'quadro inteiro', blur: 'fundo desfocado', track: 'seguir rosto',
  track_blur: 'seguir rosto com fundo desfocado', streamer: 'só o streamer', split: 'webcam + jogo', mix: 'por trecho'};
$('profileSave').onclick = () => {
  const o = currentOptions();
  $('profileSummary').textContent = [o.format, FRAMING_NAMES[o.framing] || o.framing,
    o.subtitles ? 'legenda ' + (o.subtitle_style === 'dinamica' ? 'animada' : 'clássica') : 'sem legenda',
    o.filter ? 'filtro ' + (state.filters.find((f) => f.id === o.filter)?.label || o.filter) : null,
    o.normalize_audio ? 'volume normalizado' : null, o.hook ? 'gancho' : null].filter(Boolean).join(' · ');
  $('profileName').value = '';
  $('profileChannel').value = state.project?.channel || state.project?.community?.video?.channel || '';
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
    const s = await api('/api/settings', 'PUT', {export_profiles: [...saved,
      {name, ...currentOptions(), channel: $('profileChannel').value.trim() || null}]});
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
    subtitle_look: lookIsDefault(subtitleLook()) ? null : subtitleLook(),
    reactions: reactionOptions(),
    normalize_audio: $('normalize').checked, hook: $('hook').checked, acknowledge_rights: rights,
    edit_plan: exportPlanName(), filter: state.filter === 'nenhum' ? null : state.filter,
    keep_framing: true,   // o enquadramento escolhido vale mesmo com plano de edição
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

// Aparência da legenda (fonte, tamanho, cores, fundo): lembrada entre sessões e enviada na exportação.
// O padrão é a legenda de sempre; a amostra usa a mesma fonte e as mesmas cores do vídeo exportado.
const LOOK_DEFAULT = {font: 'Arial', size: 'm', color: '#ffffff', highlight: '#04f827', outline: '#000000',
  background: 'contorno', case: 'auto', position: 'auto', y: 0.5};
const LOOK_INPUTS = {font: 'subFont', size: 'subSize', color: 'subColor', highlight: 'subHighlight',
  outline: 'subOutline', background: 'subBackground', case: 'subCase', position: 'subPosition', y: 'subY'};
const HEAVY_FONTS = ['Impact', 'Arial Black', 'Montserrat ExtraBold', 'Montserrat Black', 'Anton', 'Bebas Neue'];   // já são pesadas: o vídeo não aplica negrito nelas
function subtitleLook() {
  const look = {};
  for (const [key, id] of Object.entries(LOOK_INPUTS)) look[key] = $(id).value;
  look.y = Number(look.y) / 100;   // a tela usa porcentagem; o vídeo, fração da altura
  return look;
}
// A altura só conta na posição "Escolher a altura" (e aí a posição já difere do padrão).
const lookIsDefault = (look) => Object.keys(LOOK_DEFAULT).every((k) => k === 'y' ||
  String(look[k]).toLowerCase() === LOOK_DEFAULT[k].toLowerCase());
function rgba(hex, alpha) {
  const n = parseInt(hex.slice(1), 16);
  return 'rgba(' + (n >> 16) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + alpha + ')';
}
function renderLook() {
  const look = subtitleLook(), animated = $('subtitleStyle').value === 'dinamica';
  const box = look.background === 'caixa', bare = look.background === 'nenhum';
  const sample = $('subSample');
  sample.style.fontFamily = '"' + look.font + '", Arial, sans-serif';
  sample.style.fontSize = {p: 19, m: 22, g: 26}[look.size] + 'px';
  sample.style.fontWeight = HEAVY_FONTS.includes(look.font) ? '400' : '700';
  sample.style.textTransform = look.case === 'upper' || (look.case === 'auto' && animated) ? 'uppercase' : 'none';
  const stroke = [-2, 0, 2].flatMap((x) => [-2, 0, 2].map((y) => x + 'px ' + y + 'px 0 ' + look.outline)).join(',');
  [...sample.children].forEach((span, i) => {
    span.style.color = animated && i === 1 ? look.highlight : look.color;
    span.style.textShadow = box || bare ? 'none' : stroke;
    span.style.background = box ? rgba(look.outline, 0.85) : 'transparent';
  });
  $('subHighlightField').hidden = !animated;
  $('subOutlineField').hidden = bare;
  $('subOutlineLabel').textContent = box ? 'Caixa' : 'Contorno';
  $('subLookReset').hidden = lookIsDefault(look);
  renderPosition(look);
  drawSubPreview();
}
// Tela pequena no formato de saída com a faixa da legenda (aproximada; a Prévia mostra o resultado exato).
function renderPosition(look) {
  const media = state.project?.media, fmt = state.format;
  const ratio = fmt === '9:16' ? 9 / 16 : fmt === '16:9' ? 16 / 9 : fmt === '1:1' ? 1
    : (media?.width && media?.height ? media.width / media.height : 16 / 9);
  const portrait = ratio < 1 / 1.1;
  const w = ratio < 1 ? 128 * ratio : 150, h = ratio < 1 ? 128 : 150 / ratio;
  const screen = $('posScreen'), bar = $('posBar');
  screen.style.width = w.toFixed(1) + 'px';
  screen.style.height = $('subY').style.height = h.toFixed(1) + 'px';
  screen.classList.toggle('portrait', portrait);
  const at = lookAt(look, portrait);
  bar.style.top = (at * 100).toFixed(1) + '%';
  bar.style.height = (portrait ? 7 : 13) + '%';
  bar.style.background = look.color;
  bar.style.boxShadow = '0 0 0 1.5px ' + look.outline;
  bar.classList.toggle('auto', look.position === 'auto');
}
// Altura (fração da tela) do meio da legenda em cada posição, aproximada do que o vídeo exportado faz.
function lookAt(look, portrait) {
  const hook = $('hook').checked;
  return {auto: portrait ? 0.68 : 0.82, top: portrait ? (hook ? 0.3 : 0.19) : (hook ? 0.45 : 0.14),
    center: 0.5, bottom: portrait ? 0.78 : 0.86,
    custom: Math.min(0.92, Math.max(0.08, look.y))}[look.position] ?? 0.68;   // o vídeo não deixa a legenda sair da tela
}

// Prévia da legenda no player: as linhas do corte (as mesmas da exportação) desenhadas sobre o vídeo original.
const subPrev = {key: null, lines: [], line: -1};
async function loadPreviewWords(c) {
  const key = state.project.id + ':' + c.id + ':' + c.start + ':' + c.end;
  if (subPrev.key === key) return;
  Object.assign(subPrev, {key, lines: [], line: -1});
  try {
    const d = await api('/api/projects/' + state.project.id + '/words?start=' + c.start + '&end=' + c.end);
    if (subPrev.key === key) { subPrev.lines = d.lines || []; drawSubPreview(); }
  } catch { /* sem prévia: a exportação continua igual */ }
}
function drawSubPreview() {
  const el = $('subPreview'), c = current();
  if (!($('subPreviewOn').checked && $('subtitles').checked && c && state.project)) { el.hidden = true; return; }
  loadPreviewWords(c);
  const t = video.currentTime;
  const k = subPrev.lines.findIndex((l) => t >= l[0].start - 0.05 && t <= l[l.length - 1].end + 0.35);
  if (k < 0) { el.hidden = true; subPrev.line = -1; return; }
  const look = subtitleLook(), animated = $('subtitleStyle').value === 'dinamica';
  const box = $('videoBox').getBoundingClientRect(), vw = video.videoWidth || 16, vh = video.videoHeight || 9;
  const scale = Math.min(box.width / vw, box.height / vh), w = vw * scale, h = vh * scale, portrait = h > w * 1.1;
  el.style.left = (box.width - w) / 2 + 'px';
  el.style.width = w + 'px';
  el.style.top = (box.height - h) / 2 + lookAt(look, portrait) * h + 'px';
  el.style.fontFamily = '"' + look.font + '", Arial, sans-serif';
  el.style.fontSize = w * (portrait ? 0.078 : 0.05) * {p: 0.85, m: 1, g: 1.18}[look.size] + 'px';
  el.style.fontWeight = HEAVY_FONTS.includes(look.font) ? '400' : '700';
  el.style.textTransform = look.case === 'upper' || (look.case === 'auto' && animated) ? 'uppercase' : 'none';
  const box_ = look.background === 'caixa', bare = look.background === 'nenhum';
  const px = Math.max(1, Math.round(w / 400));
  const stroke = [-px, 0, px].flatMap((x) => [-px, 0, px].map((y) => x + 'px ' + y + 'px 0 ' + look.outline)).join(',');
  el.replaceChildren(...subPrev.lines[k].flatMap((word, i) => {
    const span = document.createElement('span');
    span.textContent = word.text;
    span.style.color = animated && t >= word.start && t < word.end ? look.highlight : look.color;
    span.style.textShadow = box_ || bare ? 'none' : stroke;
    span.style.background = box_ ? rgba(look.outline, 0.85) : 'transparent';
    return i ? [document.createTextNode(' '), span] : [span];
  }));
  if (k !== subPrev.line) {
    subPrev.line = k;
    el.classList.remove('pop');
    void el.offsetWidth;   // reinicia a animação de entrada da frase
    if (animated) el.classList.add('pop');
  }
  el.hidden = false;
}
$('subPreviewOn').checked = store.get('subPreview') === 'on';
$('subPreviewOn').addEventListener('change', () => { store.set('subPreview', $('subPreviewOn').checked ? 'on' : 'off'); drawSubPreview(); });
for (const ev of ['timeupdate', 'seeked', 'loadedmetadata']) video.addEventListener(ev, drawSubPreview);
video.addEventListener('play', () => {
  const loop = () => { drawSubPreview(); if (!video.paused) requestAnimationFrame(loop); };
  loop();
});
for (const id of ['subtitles', 'subtitleStyle', 'hook']) $(id).addEventListener('change', drawSubPreview);
window.addEventListener('resize', drawSubPreview);

function setLook(look) {
  for (const [key, id] of Object.entries(LOOK_INPUTS)) {
    const value = look?.[key], el = $(id);
    if (key === 'y') {   // fração da altura (0–1) guardada; o controle usa porcentagem
      el.value = Number.isFinite(value) ? Math.round(value * 100) : 50;
      continue;
    }
    const valid = el.tagName === 'SELECT' ? [...el.options].some((o) => o.value === value)
      : /^#[0-9a-f]{6}$/i.test(value || '');
    el.value = valid ? value : LOOK_DEFAULT[key];
  }
  renderLook();
}
try { setLook(JSON.parse(store.get('subtitleLook') || 'null')); } catch { setLook(null); }
// Mexer na altura (controle ou tela pequena) escolhe a posição "Escolher a altura".
$('subY').addEventListener('input', () => { $('subPosition').value = 'custom'; });
function pickHeight(e) {
  const r = $('posScreen').getBoundingClientRect();
  $('subY').value = Math.round(((e.clientY - r.top) / r.height) * 100);
  $('subY').dispatchEvent(new Event('input'));
}
$('posScreen').addEventListener('pointerdown', (e) => {
  if ($('lookBox').disabled) return;
  $('posScreen').setPointerCapture(e.pointerId);
  pickHeight(e);
});
$('posScreen').addEventListener('pointermove', (e) => {
  if ($('posScreen').hasPointerCapture(e.pointerId)) pickHeight(e);
});
$('hook').addEventListener('change', renderLook);
window.addEventListener('formatchange', renderLook);
for (const id of Object.values(LOOK_INPUTS)) {
  $(id).addEventListener('input', () => { renderLook(); store.set('subtitleLook', JSON.stringify(subtitleLook())); });
}
$('subLookReset').onclick = () => { setLook(null); store.set('subtitleLook', JSON.stringify(subtitleLook())); };
$('subtitleStyle').addEventListener('change', renderLook);

// Momentos de pico na legenda animada: lembrado entre sessões.
function reactionOptions() {
  if (!$('subtitles').checked || $('subtitleStyle').value !== 'dinamica' || !$('rxPeaks').checked) return null;
  return {peaks: true};
}
function syncReactions() {
  $('reactBox').disabled = !$('subtitles').checked || $('subtitleStyle').value !== 'dinamica';
}
try { $('rxPeaks').checked = (JSON.parse(store.get('reactions') || '{}') || {}).peaks === true; } catch { /* desligado */ }
$('rxPeaks').addEventListener('change', () => store.set('reactions', JSON.stringify({peaks: $('rxPeaks').checked})));
$('subtitleStyle').addEventListener('change', syncReactions);
syncReactions();

function syncSubtitleStyle() {
  const plan = current()?.edit_plans?.[exportPlanName()];
  const planned = Boolean(plan);
  if (plan) {
    $('subtitleStyle').value = plan.settings.subtitle_style;
    $('normalize').checked = plan.settings.normalize_audio;
    // O plano sugere o enquadramento só quando é escolhido; depois ele continua editável e a escolha vale.
    const key = state.project?.id + ':' + current()?.id + ':' + exportPlanName();
    if (state.planFramingKey !== key) {
      state.planFramingKey = key;
      $('framing').value = plan.settings.framing;
      $('framing').dispatchEvent(new Event('change'));
    }
  } else {
    state.planFramingKey = null;
  }
  $('framing').disabled = false;
  $('normalize').disabled = planned;
  if (state.profiles) profileTouched();
  $('subtitleStyle').disabled = !$('subtitles').checked || planned;
  $('subtitleStyleNote').hidden = !planned || !$('subtitles').checked;
  $('refineSubtitles').disabled = !$('subtitles').checked;
  $('lookBox').disabled = !$('subtitles').checked;
  renderLook();   // o plano pode ter trocado o estilo (a cor da palavra falada só vale na animada)
  syncReactions();
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
  return (j.format || '') + ' · ' + (j.message || 'Processando') + ' · ' + Math.round((j.progress || 0) * 100) + '%';
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
      row(clipLabel(j.clip_id), (i + 1) + 'º na fila · ' + (j.format || ''),
        {actions: cancel(j)})).join('')) +
    group('done', 'Concluídas', done, done.map(({c, format}) =>
      row(clipLabel(c.id), format + (c.exports[format]?.validation?.duration ? ' · ' + fmt(c.exports[format].validation.duration) : ''), {
        tone: 'ok',
        actions: '<span class="qactions"><button type="button" class="iconbtn" title="Assistir" aria-label="Assistir" ' +
          'data-watch="' + c.id + '" data-format="' + escape(format) + '"><svg class="ic"><use href="#i-eye"/></svg></button>' +
          '<a class="iconbtn" title="Baixar MP4" aria-label="Baixar MP4" href="' + downloadUrl(p.id, c.id, format) +
          '"><svg class="ic"><use href="#i-download"/></svg></a>' +
          (c.exports[format]?.youtube
            ? '<a class="iconbtn" title="Abrir no YouTube" aria-label="Abrir no YouTube" target="_blank" rel="noopener" href="' +
              escape(c.exports[format].youtube.url) + '"><svg class="ic"><use href="#i-external"/></svg></a>'
            : '<button type="button" class="iconbtn" title="Publicar no YouTube" aria-label="Publicar no YouTube" ' +
              'data-yt="' + c.id + '" data-format="' + escape(format) + '"><svg class="ic"><use href="#i-upload"/></svg></button>') +
          '<button type="button" class="iconbtn danger" title="Excluir esta exportação" aria-label="Excluir esta exportação" ' +
          'data-delexport="' + c.id + '" data-format="' + escape(format) + '"><svg class="ic"><use href="#i-trash"/></svg></button></span>',
      })).join('') + (done.length > 1 ? '<li class="qrow qbulk"><button type="button" class="btn ghost danger" ' +
        'data-delallexports>Excluir todas as exportações</button></li>' : ''), done.length <= 3) +
    group('failed', 'Com erro', failed, failed.map((j) =>
      row(clipLabel(j.clip_id), (j.format || '') + ' · ' + (j.error || 'A exportação não terminou.'), {
        tone: 'error',
        actions: j.kind === 'export' ? '<button type="button" class="btn ghost" data-retry="' + j.id +
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
    toast(finished.length === 1 ? clipLabel(finished[0].clip_id) + (finished[0].kind === 'youtube' ? ' publicado no YouTube ('
      : ' exportado (') + finished[0].format + ').'
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
    return;
  }
  const yt = e.target.closest('[data-yt]');
  if (yt) { openYoutube(Number(yt.dataset.yt), yt.dataset.format); return; }
  const del = e.target.closest('[data-delexport], [data-delallexports]');
  if (del) deleteExports(del.dataset.delallexports !== undefined ? null
    : [{clip: Number(del.dataset.delexport), format: del.dataset.format}]);
});
