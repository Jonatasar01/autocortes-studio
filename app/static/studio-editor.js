'use strict';
// AutoCortes Studio — Edição de cortes, linha do tempo, forma de onda e player.
// Scripts clássicos carregados em ordem por studio.html: compartilham as variáveis globais do studio.js.

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
    // Nome lido pelo leitor de tela (o texto visível junta número, título e duração sem separação).
    b.setAttribute('aria-label', 'Corte ' + (c.rank || c.id) + ': ' + String(c.title || '').replace(/[\s.,;:!?…]+$/, '') +
      ', de ' + fmt(c.start) + ' a ' +
      fmt(c.end) + (c.score != null ? ', nota ' + Math.round(c.score) : ''));
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
    ctx.fillStyle = getComputedStyle(lane).getPropertyValue('--wave').trim() || '#5b86de';
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
  toast('O navegador não conseguiu reproduzir este vídeo. A edição e a exportação continuam funcionando. ' +
    'Projetos novos são convertidos para MP4 sem perda na análise; neste, use Analisar de novo.', true);
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
for (const b of document.querySelectorAll('[data-jump]')) b.onclick = () => nudge(Number(b.dataset.jump));
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
  else if (k === 'j') nudge(e.shiftKey ? -30 : -10);
  else if (k === 'l') nudge(e.shiftKey ? 30 : 10);
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
    window.dispatchEvent(new Event('formatchange'));   // a tela pequena da posição da legenda segue o formato
  };
}
