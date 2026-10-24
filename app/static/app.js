'use strict';
/* AutoCortes Studio — interface local. Todo texto vindo do servidor/vídeo é escapado. */
const $ = (selector, parent = document) => parent.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const state = { id: null, project: null, timer: null, renderedId: null, health: null, previewEnd: null, settings: null };

const STAGES = [['validando', 'Validação do arquivo'], ['musica', 'Verificação musical (se autorizada)'],
  ['extraindo_audio', 'Extração do áudio'], ['transcrevendo', 'Transcrição local'], ['cenas', 'Mudanças de cena'],
  ['selecionando', 'Seleção dos trechos'], ['curadoria_ia', 'Seleção e avaliação com IA'], ['ia_local', 'Refinamento com IA local']];
const PHASE = { queued: 'NA FILA', analyzing: 'ANALISANDO', ready: 'EDIÇÃO DISPONÍVEL', error: 'ERRO', cancelled: 'CANCELADO', interrupted: 'INTERROMPIDO' };
const FORMATS = { '9:16': 'Vertical 9:16', '16:9': 'Horizontal 16:9', '1:1': 'Quadrado 1:1', original: 'Proporção original' };
const STATUS_CLASS = { declarado: 'ok', revisar: 'warn', risco_alto: 'high', nao_verificado: 'unk', nao_aplicavel: 'na' };
const RISK = { alto: ['RISCO RELEVANTE — REVISÃO PRIORITÁRIA', 'high'], revisao: ['PENDÊNCIAS A CONFERIR', ''], informado_sem_alertas: ['SEM PENDÊNCIAS DECLARADAS', 'low'] };

function notify(message, error = false) {
  const t = $('#toast'); t.textContent = message; t.hidden = false; t.classList.toggle('error', error);
  clearTimeout(notify.timer); notify.timer = setTimeout(() => { t.hidden = true; }, error ? 9000 : 5000);
}
function phase(text) { $('#phase').textContent = text; }
function fmtTime(sec) {
  const s = Math.max(0, Number(sec) || 0); const m = Math.floor(s / 60);
  return `${String(m).padStart(2, '0')}:${(s - m * 60).toFixed(1).padStart(4, '0')}`;
}
async function api(url, { method = 'GET', json } = {}) {
  const options = { method, headers: {} };
  if (method !== 'GET') options.headers['X-AutoCortes'] = '1';
  if (json !== undefined) { options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(json); }
  const response = await fetch(url, options);
  let data;
  try { data = await response.json(); } catch { data = { detail: `Erro ${response.status} na resposta do servidor.` }; }
  if (!response.ok) {
    const err = new Error(typeof data.detail === 'string' ? data.detail : 'Falha na requisição.');
    err.code = data.code; throw err;
  }
  return data;
}

/* ------------------------------------------------------------------ diagnóstico e biblioteca */
async function loadHealth() {
  try {
    const h = await api('/api/health'); state.health = h;
    const chip = $('#sysStatus');
    const parts = [h.ffmpeg.ffmpeg ? 'FFMPEG ✓' : 'FFMPEG AUSENTE', h.transcription ? 'TRANSCRIÇÃO ✓' : 'SEM TRANSCRIÇÃO',
      h.ollama_enabled ? 'OLLAMA ON' : 'OLLAMA OFF'];
    chip.innerHTML = `<span class="statusdot ${h.ok ? '' : 'bad'}"></span> ${esc(parts.join(' · '))}`;
    chip.title = `Legendas (libass): ${h.ffmpeg.libass ? 'disponível' : 'indisponível'} · Disco livre: ${h.free_disk_gb} GB`;
    $('#limitInfo').textContent = `até ${h.limits.max_upload_gb} GB`;
    renderWatchChip();
    if (!h.ok) notify('FFmpeg/ffprobe não encontrados. Instale o FFmpeg e reinicie o aplicativo.', true);
  } catch (e) { notify('Não foi possível consultar o diagnóstico: ' + e.message, true); }
}
async function loadLibrary() {
  try {
    const { projects } = await api('/api/projects');
    const list = $('#library');
    if (!projects.length) { list.innerHTML = '<li class="hint">Nenhum projeto ainda.</li>'; return; }
    list.innerHTML = projects.slice(0, 20).map(p => `<li><button type="button" class="lib-item ${p.id === state.id ? 'active' : ''}" data-id="${esc(p.id)}">
      <span class="lib-name">${esc(p.name)}</span><span class="lib-meta">${esc(PHASE[p.status] || p.status)} · ${p.clips} cortes · ${new Date((p.created_at || 0) * 1000).toLocaleString('pt-BR')}</span>
      ${p.origin === 'pasta_entrada' || p.autopilot ? `<span class="lib-meta">${p.origin === 'pasta_entrada' ? '📂 pasta de entrada' : ''}${p.origin === 'pasta_entrada' && p.autopilot ? ' · ' : ''}${p.autopilot ? '🤖 ' + esc(AP_LABEL[p.autopilot] || p.autopilot) : ''}</span>` : ''}</button></li>`).join('');
    for (const b of list.querySelectorAll('.lib-item')) b.onclick = () => openProject(b.dataset.id);
  } catch (e) { /* biblioteca é auxiliar */ }
}

/* ------------------------------------------------------------------ envio */
const fileInput = $('#file');
function showFile() { $('#filename').textContent = fileInput.files?.[0]?.name || 'Nenhum arquivo selecionado'; }
fileInput.addEventListener('change', showFile);
const drop = $('#dropzone');
drop.addEventListener('keydown', e => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInput.click(); }
});
['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('drag'); }));
['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('drag'); }));
drop.addEventListener('drop', e => { if (e.dataTransfer.files.length) { fileInput.files = e.dataTransfer.files; showFile(); } });

$('#form').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.target; const file = fileInput.files?.[0];
  const linkMode = $('#sourceMode').value === 'link';
  if (!file && !linkMode) { notify('Selecione um arquivo de vídeo.', true); return; }
  if (!form.reportValidity()) return;
  const limit = (state.health?.limits?.max_upload_gb || 4) * 1024 ** 3;
  if (!linkMode && file.size > limit) { notify('Arquivo maior que o limite configurado.', true); return; }
  const params = new URLSearchParams();
  for (const el of form.elements) {
    if (!el.name || el.type === 'file') continue;
    if (el.name === 'autopilot') params.set('autopilot', el.checked ? 'true' : 'false');
    else if (el.type === 'checkbox') { if (el.checked) params.set(el.name, 'true'); } else params.set(el.name, el.value);
  }
  const button = $('#submit'); button.disabled = true;
  if (linkMode) {
    try {
      const job = await api('/api/imports/link', {method: 'POST', json: {url: $('#videoUrl').value.trim(), options: Object.fromEntries(params)}});
      localStorage.setItem('autocortes-import', job.id); watchImport(job.id);
      notify(job.duplicate ? 'Este link já está sendo importado.' : 'Link na fila. A análise começará após o download.');
      refreshAutomation();
    } catch (e) { notify(e.message, true); }
    finally { button.disabled = false; }
    return;
  }
  $('#uploadBox').hidden = false; phase('ENVIANDO ARQUIVO');
  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/projects?' + params.toString());
  xhr.setRequestHeader('X-AutoCortes', '1');
  xhr.setRequestHeader('X-Filename', encodeURIComponent(file.name));
  xhr.setRequestHeader('Content-Type', 'application/octet-stream');
  xhr.upload.onprogress = e => {
    if (!e.lengthComputable) return;
    const pct = Math.round(e.loaded / e.total * 100);
    $('#uploadBar').value = pct; $('#uploadText').textContent = pct < 100 ? `${pct}% enviado` : 'Verificando o arquivo…';
  };
  xhr.onload = () => {
    button.disabled = false; $('#uploadBox').hidden = true;
    let data = {}; try { data = JSON.parse(xhr.responseText); } catch { /* resposta inválida */ }
    if (xhr.status === 413 && !data.detail) data.detail = 'Arquivo grande demais para o acesso pela internet (Cloudflare aceita até 100 MB por envio). Use a importação por link ou envie direto no PC.';
    if (xhr.status !== 200) { phase('ERRO'); notify(data.detail || `Falha no envio (${xhr.status}).`, true); return; }
    if (data.warnings?.length) notify(data.warnings.join(' '));
    openProject(data.id); loadLibrary();
  };
  xhr.onerror = () => { button.disabled = false; $('#uploadBox').hidden = true; phase('ERRO'); notify('Falha de conexão durante o envio.', true); };
  xhr.send(file);
});

/* ------------------------------------------------------------------ projeto */
function openProject(id) {
  if (!/^[a-f0-9]{32}$/.test(id || '')) return;
  state.id = id; state.project = null; state.renderedId = null;
  $('#ackRights').checked = false;
  history.replaceState({}, '', `/?projeto=${id}`);
  $('#workspace').hidden = true; $('#empty').hidden = true;
  phase('CARREGANDO PROJETO'); poll(); loadLibrary();
}
function schedule(ms) { clearTimeout(state.timer); state.timer = setTimeout(poll, ms); }
async function poll() {
  if (!state.id) return;
  let p;
  try { p = await api(`/api/projects/${state.id}`); } catch (e) {
    notify(e.message, true); phase('ERRO');
    if (e.message.includes('não encontrado')) { state.id = null; history.replaceState({}, '', '/'); $('#empty').hidden = false; }
    return;
  }
  state.project = p; phase(PHASE[p.status] || 'PROCESSANDO');
  if (p.status === 'queued' || p.status === 'analyzing') { renderProgress(p); schedule(1200); return; }
  if (p.status !== 'ready') { renderProgress(p); return; }
  $('#progressPanel').hidden = true;
  if (state.renderedId !== p.id) { renderWorkspace(p); loadLibrary(); }
  updateClipJobs(p);
  renderTimeline(p);
  renderAdvanced(p);
  renderAutopilot(p);
  const apBusy = ['aguardando_analise', 'executando'].includes(p.autopilot?.status);
  if (apBusy || (p.jobs || []).some(j => ['queued', 'running', 'cancelling'].includes(j.status))) schedule(1000);
  else if (state.apWasBusy) loadLibrary();
  state.apWasBusy = apBusy;
}

function renderProgress(p) {
  $('#workspace').hidden = true; $('#empty').hidden = true; $('#progressPanel').hidden = false;
  const job = p.analysis || {}; const pct = Math.round((job.progress || 0) * 100);
  const running = ['queued', 'analyzing'].includes(p.status);
  $('#progressTitle').textContent = running ? `Analisando “${p.name}”` : `Análise de “${p.name}”`;
  $('#analysisBar').value = pct;
  $('#progressText').textContent = running ? `${pct}% · ${job.message || 'Aguardando na fila'}` : '';
  const seen = new Set((p.analysis_events || []).map(e => e.stage));
  const current = job.stage;
  $('#stageList').innerHTML = STAGES.map(([key, label]) => {
    const cls = key === current && running ? 'current' : seen.has(key) ? 'done' : '';
    return `<li class="${cls}">${esc(label)}</li>`;
  }).join('');
  $('#cancelAnalysis').hidden = !running;
  $('#retryAnalysis').hidden = running;
  const err = $('#progressError');
  err.hidden = running; err.textContent = running ? '' : (p.error || 'A análise não foi concluída.');
}
$('#cancelAnalysis').onclick = async () => {
  const id = state.project?.analysis?.id; if (!id) return;
  try { await api(`/api/jobs/${id}/cancel`, { method: 'POST' }); notify('Cancelamento solicitado.'); schedule(500); } catch (e) { notify(e.message, true); }
};
$('#retryAnalysis').onclick = async () => {
  try { await api(`/api/projects/${state.id}/reanalyze`, { method: 'POST' }); schedule(300); } catch (e) { notify(e.message, true); }
};
$('#deleteProject').onclick = async () => {
  if (!state.id || !confirm('Excluir este projeto, o vídeo importado e todos os cortes exportados?')) return;
  try { await api(`/api/projects/${state.id}`, { method: 'DELETE' }); notify('Projeto excluído.'); state.id = null; history.replaceState({}, '', '/'); $('#workspace').hidden = true; $('#empty').hidden = false; phase('AGUARDANDO VÍDEO'); loadLibrary(); } catch (e) { notify(e.message, true); }
};
$('#refreshLib').onclick = loadLibrary;

/* ------------------------------------------------------------------ área de trabalho */
function renderWorkspace(p) {
  state.renderedId = p.id;
  $('#empty').hidden = true; $('#workspace').hidden = false;
  const video = $('#video'); video.src = `/api/projects/${p.id}/source`;
  $('#sourceName').textContent = p.name;
  const m = p.media;
  $('#metadata').innerHTML = `<p>⏱ ${esc(fmtTime(m.duration))} (${Math.round(m.duration)} s)</p><p>▣ ${esc(m.width)} × ${esc(m.height)} · ${esc(m.orientation || '')}${m.rotation ? ` · rotação ${esc(m.rotation)}°` : ''}</p>
    <p>🎞 ${m.fps ? esc(m.fps) + ' fps' : 'fps desconhecido'}${m.vfr ? ' (variável)' : ''} · ${esc(m.video_codec || '')}</p><p>♫ ${m.audio ? 'Com áudio' + (m.audio_streams > 1 ? ` (${m.audio_streams} trilhas)` : '') : 'Sem áudio'}</p>`;
  const sel = p.selection || {};
  $('#aiStatus').textContent = `${p.transcript_status || ''} ${sel.mode === 'temporal' ? 'Seleção TEMPORAL (sem análise de conteúdo).' : 'Seleção por frases completas da transcrição.'} ${p.curation?.state && p.curation.state !== 'disabled' ? p.curation.message + ' ' : ''}${p.ranker_status || ''}`;
  $('#warnings').innerHTML = (p.warnings || []).map(w => `<li>${esc(w)}</li>`).join('');
  renderRights(p);
  renderClips(p);
  renderTimeline(p);
  $('#bundleDownload').href = `/api/projects/${p.id}/bundle`;
}

function renderRights(p) {
  const r = p.rights || {};
  const [label, cls] = RISK[r.level] || ['REVISÃO', ''];
  const badge = $('#riskBadge'); badge.textContent = label; badge.className = 'risk-badge ' + cls;
  $('#copyrightNote').textContent = r.confidence || '';
  $('#rightsRows').innerHTML = (r.items || []).map(i => `<tr><td>${esc(i.label)}</td><td><span class="status ${STATUS_CLASS[i.status] || ''}">${esc(i.status_label)}</span></td><td>${esc(i.reason)}<br><small class="muted">Verificação: ${esc(i.verification)}</small></td><td>${esc(i.recommended_action)}</td></tr>`).join('');
  const mr = p.music_review || {};
  const matches = (mr.matches || []).map(x => `${x.title} — ${x.artist}${x.uncertain ? ' (correspondência incerta)' : ''}`).join('; ');
  $('#music').textContent = 'Reconhecimento musical: ' + (mr.message || 'não verificado.') + (matches ? ' ' + matches : '');
  $('#disclaimer').textContent = (r.disclaimer || '') + ' A ferramenta não tem acesso ao Content ID.';
  $('#ackBox').hidden = !r.requires_acknowledgement;
  $('#report').href = `/api/projects/${p.id}/report`;
}

function scoreBadge(c) {
  if (c.selection_mode === 'temporal' || c.score === null || c.score === undefined) return '<span class="score fallback" title="Sem análise de conteúdo">CORTE TEMPORAL</span>';
  const how = c.ai_curation ? ' + avaliação da IA (' + c.ai_curation + ')' : (c.ai_score !== undefined ? ' + IA local' : '');
  return `<span class="score" title="Pontuação heurística${esc(how)}">${esc(c.score)} / 100</span>`;
}
function reasonsBlock(c) {
  const items = (c.reasons || []).map(r => `<li>${esc(r)}</li>`).join('');
  const bd = c.score_breakdown ? Object.entries(c.score_breakdown).map(([k, v]) => `${esc(k.replace(/_/g, ' '))}: ${esc(v)}`).join(' · ') : '';
  const ai = c.ai_reason ? `<p><strong>IA local (Ollama):</strong> ${esc(c.ai_reason)} — nota ${esc(c.ai_score)}</p>`
    : c.ai_curation ? `<p><strong>Curadoria com IA:</strong> nota da IA ${esc(c.ai_score ?? '—')} · heurística ${esc(c.heuristic_score ?? '—')}${c.hook_text ? ` · gancho: “${esc(c.hook_text)}”` : ''}${(c.hashtags || []).length ? ` · ${esc(c.hashtags.join(' '))}` : ''}</p>` : '';
  return `<details class="reasons"><summary>Por que este trecho?</summary><ul>${items}</ul>${bd ? `<p class="muted">${bd}</p>` : ''}${ai}</details>`;
}

function narrationPanel(c) {
  const h = state.health || {}; const ok = h.openai_configured;
  const styles = h.narration?.styles || { recap: 'Recap narrado', comentario: 'Comentário', documentario: 'Documentário' };
  return `<details class="narration" ${c.narration ? 'open' : ''}><summary>🎙 Narração com ChatGPT <span class="n-badge muted">${c.narration ? '· roteiro pronto' : ''}</span></summary>
    ${ok ? '' : '<p class="hint warn-text">Defina a variável OPENAI_API_KEY e reinicie o aplicativo para ativar a narração.</p>'}
    <div class="narr-gen">
      <label>Estilo<select class="n-style">${Object.entries(styles).map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join('')}</select></label>
      <label class="grow">Contexto (opcional)<input class="n-context" type="text" maxlength="500" placeholder="Ex.: nomes dos personagens, assunto, o que aconteceu antes"></label>
      <label class="option"><input class="n-frames" type="checkbox"> Enviar quadros do vídeo (a IA “vê” as cenas)</label>
      <label class="option"><input class="n-consent" type="checkbox"> Autorizo enviar a transcrição deste trecho (e os quadros, se marcado) à OpenAI</label>
      <button type="button" class="tertiary n-generate" ${ok ? '' : 'disabled'}>✨ ${c.narration ? 'Gerar novo roteiro' : 'Gerar roteiro'}</button>
    </div>
    <div class="narr-script"></div></details>`;
}

function renderNarration(el, clip) {
  const script = clip.narration; const box = $('.narr-script', el);
  const stamp = script ? String(script.edited_at || script.created_at) : '';
  if (el.dataset.narrStamp === stamp) return;
  el.dataset.narrStamp = stamp;
  $('.use-narr', el).disabled = !script?.segments?.length;
  $('.n-badge', el).textContent = script ? '· roteiro pronto' : '';
  if (!script) { box.innerHTML = ''; return; }
  box.innerHTML = `<div class="narr-head"><p class="eyebrow">ROTEIRO ${script.edited_manually ? '(EDITADO)' : `(${esc(script.model)})`}</p>
      ${script.title ? `<button type="button" class="link-btn n-usetitle" title="Usar como título do corte">Título sugerido: “${esc(script.title)}” ↵</button>` : ''}</div>
    ${(script.warnings || []).map(w => `<p class="hint warn-text">⚠ ${esc(w)}</p>`).join('')}
    <ol class="narr-list">${script.segments.map(s => `<li><div class="narr-times"><input class="n-start" type="number" step="0.1" min="0" value="${esc(s.start)}" aria-label="Início do bloco (s)"><span>→</span><input class="n-end" type="number" step="0.1" min="0" value="${esc(s.end)}" aria-label="Fim do bloco (s)"></div><textarea class="n-text" rows="2" maxlength="600">${esc(s.text)}</textarea></li>`).join('')}</ol>
    <div class="row-gap"><button type="button" class="tertiary n-save">Salvar roteiro</button><span class="hint">Tempos relativos ao início do corte. Enviado: ${esc(script.consent?.sent || '')}.</span></div>`;
  const useTitle = $('.n-usetitle', box);
  if (useTitle) useTitle.onclick = () => { $('.clip-title', el).value = script.title; notify('Título aplicado — clique em “Salvar ajuste”.'); };
  $('.n-save', box).onclick = async () => {
    const segments = [...box.querySelectorAll('.narr-list li')].map(li => ({ start: Number($('.n-start', li).value), end: Number($('.n-end', li).value), text: $('.n-text', li).value.trim() })).filter(x => x.text);
    try {
      const saved = await api(`/api/projects/${state.id}/clips/${el.dataset.id}/narration`, { method: 'PUT', json: { segments } });
      const c = clipData(state.project, Number(el.dataset.id)); if (c) c.narration = saved;
      el.dataset.narrStamp = ''; renderNarration(el, c); notify('Roteiro salvo.');
    } catch (e) { notify(e.message, true); }
  };
}

function renderClips(p) {
  const hasTranscript = (p.transcript || []).length > 0;
  $('#cutCount').textContent = `${p.clips.length} CORTE${p.clips.length === 1 ? '' : 'S'}`;
  const defaultFormat = '9:16'; // Shorts/Reels/TikTok; o usuário escolhe outro formato por corte
  $('#clips').innerHTML = p.clips.map(c => `<article class="clip" data-id="${c.id}">
    <div class="clip-top"><div><h4><span class="rank">#${c.rank || c.id}</span> <span class="clip-heading">${esc(c.title)}</span></h4>
      <div class="clip-meta">CORTE ${String(c.id).padStart(2, '0')} · <span class="range">${fmtTime(c.start)} → ${fmtTime(c.end)} · ${Math.round(c.end - c.start)} s</span></div></div>${scoreBadge(c)}</div>
    <p class="clip-desc">${esc(c.description)}</p>${reasonsBlock(c)}
    <div class="edit-row">
      <label class="title-field">Título<input class="clip-title" type="text" maxlength="120" value="${esc(c.title)}"></label>
      <label class="time-field">Início (s)<input class="clip-start" type="number" min="0" step="0.1" value="${esc(c.start)}"></label>
      <button type="button" class="tertiary mark-start" title="Usar a posição atual do vídeo como início">⇤ agora</button>
      <label class="time-field">Fim (s)<input class="clip-end" type="number" min="0" step="0.1" value="${esc(c.end)}"></label>
      <button type="button" class="tertiary mark-end" title="Usar a posição atual do vídeo como fim">agora ⇥</button>
    </div>
    <div class="action-row"><button type="button" class="tertiary preview">▶ Visualizar trecho</button><button type="button" class="tertiary save">Salvar ajuste</button></div>
    ${narrationPanel(c)}
    <div class="export-row">
      <label>Formato<select class="fmt">${Object.entries(FORMATS).map(([k, v]) => `<option value="${k}" ${k === defaultFormat ? 'selected' : ''}>${v}</option>`).join('')}</select></label>
      <label>Enquadramento<select class="framing"><option value="fit">Quadro inteiro (barras)</option><option value="blur">Quadro inteiro + fundo desfocado</option><option value="track" ${state.health?.face_tracking === false ? 'disabled' : ''}>Seguir rosto (recorta as laterais)${state.health?.face_tracking === false ? ' — instale requirements-ai' : ''}</option></select></label>
      <label class="option"><input class="subtitles" type="checkbox" ${hasTranscript ? 'checked' : 'disabled'}> Legendas${hasTranscript ? '' : ' (sem transcrição)'}</label>
      <label>Estilo<select class="substyle" ${hasTranscript ? '' : 'disabled'}><option value="classica">Clássica</option><option value="dinamica" selected>Dinâmica</option></select></label>
      <label class="option" title="Frase curta no topo nos primeiros 3 segundos"><input class="hook" type="checkbox" ${c.selection_mode === 'temporal' ? 'disabled' : ''}> Gancho${c.hook_text ? '' : ' (título)'}</label>
      <label class="option"><input class="normalize" type="checkbox"> Normalizar volume</label>
      <label class="option" title="Remove pausas longas da fala (desativado quando há narração)"><input class="pauses" type="checkbox"> Encurtar pausas</label>
      <label class="option"><input class="use-narr" type="checkbox" ${c.narration?.segments?.length ? '' : 'disabled'}> Narração com voz IA</label>
      <button type="button" class="secondary export">⇩ Exportar MP4</button>
    </div>
    <div class="narr-export" hidden>
      <label>Voz<select class="n-voice">${(state.health?.narration?.voices || ['cedar']).map(v => `<option value="${esc(v)}" ${v === (state.health?.narration?.default_voice || 'cedar') ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select></label>
      <label>Áudio original<select class="n-orig"><option value="100">100%</option><option value="60" selected>60%</option><option value="30">30%</option><option value="0">Mudo</option></select></label>
      <label>Legendas de<select class="n-subsrc"><option value="narracao">Narração</option><option value="fala">Fala original</option></select></label>
      <p class="hint">O roteiro é enviado à OpenAI para gerar a voz (com cache: o mesmo texto não é cobrado duas vezes). A voz é sintética: informe ao público que é gerada por IA. O áudio original é abaixado automaticamente enquanto o narrador fala.</p>
      <label class="option"><input class="n-tts-consent" type="checkbox"> Autorizo enviar este roteiro à OpenAI para gerar a voz, com possível custo.</label>
    </div>
    <div class="job-area" aria-live="polite"></div><div class="exports-area"></div>
  </article>`).join('');
  for (const el of document.querySelectorAll('.clip')) bindClip(el, p);
}

function clipData(p, id) { return p.clips.find(c => c.id === id); }

function bindClip(el, p) {
  const id = Number(el.dataset.id);
  const start = $('.clip-start', el); const end = $('.clip-end', el); const video = $('#video');
  $('.preview', el).onclick = () => {
    video.currentTime = Number(start.value) || 0; state.previewEnd = Number(end.value) || null;
    video.play().catch(() => {}); video.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };
  $('.mark-start', el).onclick = () => { start.value = video.currentTime.toFixed(2); };
  $('.mark-end', el).onclick = () => { end.value = video.currentTime.toFixed(2); };
  const fmt = $('.fmt', el); const framing = $('.framing', el);
  fmt.onchange = () => { framing.disabled = fmt.value === 'original'; };
  async function save(silent = false) {
    const clip = await api(`/api/projects/${p.id}/clips/${id}`, { method: 'PATCH', json: { start: Number(start.value), end: Number(end.value), title: $('.clip-title', el).value.trim() } });
    Object.assign(clipData(state.project, id) || {}, clip);
    $('.clip-heading', el).textContent = clip.title;
    $('.range', el).textContent = `${fmtTime(clip.start)} → ${fmtTime(clip.end)} · ${Math.round(clip.end - clip.start)} s`;
    if (!silent) notify('Corte atualizado.');
    updateClipJobs(state.project);
    return clip;
  }
  $('.save', el).onclick = async () => { try { await save(); } catch (e) { notify(e.message, true); } };
  $('.use-narr', el).onchange = e => { $('.narr-export', el).hidden = !e.target.checked; };
  $('.n-generate', el).onclick = async () => {
    if (!$('.n-consent', el).checked) { notify('Marque a autorização de envio à OpenAI para gerar o roteiro.', true); $('.n-consent', el).focus(); return; }
    try {
      await save(true);
      const job = await api(`/api/projects/${p.id}/clips/${id}/narration`, { method: 'POST', json: {
        style: $('.n-style', el).value, context: $('.n-context', el).value, use_frames: $('.n-frames', el).checked, consent: true } });
      notify(job.duplicate ? 'O roteiro já está sendo gerado.' : 'Gerando roteiro com ChatGPT…'); schedule(400);
    } catch (e) { notify(e.message, true); }
  };
  $('.export', el).onclick = async () => {
    const needsAck = state.project?.rights?.requires_acknowledgement;
    const ack = $('#ackRights').checked;
    if (needsAck && !ack) { notify('Há riscos relevantes de direitos. Leia os alertas e marque a confirmação antes de exportar.', true); $('#ackRights').focus(); return; }
    if ($('.use-narr', el).checked && !$('.n-tts-consent', el).checked) {
      notify('Autorize o envio do roteiro para gerar a voz.', true); $('.n-tts-consent', el).focus(); return;
    }
    const button = $('.export', el); button.disabled = true;
    try {
      await save(true);
      const job = await api(`/api/projects/${p.id}/clips/${id}/export`, { method: 'POST', json: {
        format: fmt.value, framing: framing.value, subtitles: $('.subtitles', el).checked,
        subtitle_style: $('.substyle', el).value, hook: $('.hook', el).checked,
        normalize_audio: $('.normalize', el).checked, acknowledge_rights: ack,
        narration: $('.use-narr', el).checked, narration_consent: $('.n-tts-consent', el).checked,
        voice: $('.n-voice', el).value, original_volume: Number($('.n-orig', el).value),
        subtitle_source: $('.use-narr', el).checked ? $('.n-subsrc', el).value : 'fala',
        shorten_pauses: $('.pauses', el).checked && !$('.use-narr', el).checked } });
      notify(job.duplicate ? 'Essa exportação já está em andamento.' : 'Exportação adicionada à fila.');
      schedule(400);
    } catch (e) { notify(e.message, true); } finally { button.disabled = false; }
  };
}

function updateClipJobs(p) {
  for (const el of document.querySelectorAll('.clip')) {
    const id = Number(el.dataset.id); const clip = clipData(p, id); if (!clip) continue;
    renderNarration(el, clip);
    const jobs = (p.jobs || []).filter(j => (j.kind === 'export' || j.kind === 'narration') && j.clip_id === id);
    const active = jobs.filter(j => ['queued', 'running', 'cancelling'].includes(j.status));
    const lastFailed = jobs.find(j => j.status === 'failed');
    const area = $('.job-area', el);
    area.innerHTML = active.map(j => `<div class="job"><span>${esc(j.kind === 'narration' ? 'Roteiro (ChatGPT)' : (FORMATS[j.format] || j.format) + (j.narration ? ' + narração' : ''))} — ${esc(j.status === 'queued' ? 'na fila' : j.message || 'processando')}</span>
      <progress max="100" value="${Math.round((j.progress || 0) * 100)}"></progress><span>${Math.round((j.progress || 0) * 100)}%</span>
      <button type="button" class="tertiary cancel-job" data-job="${esc(j.id)}">Cancelar</button></div>`).join('') +
      (!active.length && lastFailed && lastFailed === jobs[0] ? `<p class="error">${lastFailed.kind === 'narration' ? 'Falha ao gerar o roteiro' : 'Última exportação falhou'}: ${esc(lastFailed.error)}</p>` : '');
    for (const b of area.querySelectorAll('.cancel-job')) b.onclick = async () => { try { await api(`/api/jobs/${b.dataset.job}/cancel`, { method: 'POST' }); schedule(300); } catch (e) { notify(e.message, true); } };
    const exportsList = Object.entries(clip.exports || {});
    $('.exports-area', el).innerHTML = exportsList.length ? `<p class="eyebrow">ARQUIVOS EXPORTADOS</p><ul class="exports">${exportsList.map(([f, r]) => {
      const stale = Math.abs(r.start - clip.start) > 0.01 || Math.abs(r.end - clip.end) > 0.01;
      const valid = r.validation?.valid;
      return `<li><strong>${esc(FORMATS[f] || f)}</strong> · ${esc(r.width)}×${esc(r.height)} · ${r.subtitles_burned ? 'legendas gravadas' : 'sem legendas'}${r.face_tracking ? ' · seguindo rosto' : ''}${r.ai_narration ? ' · <span class="status warn">narração IA</span>' : ''} ·
        <span class="status ${valid ? 'ok' : 'high'}">${valid ? 'validado' : 'não validado'}</span>${stale ? ' <span class="status warn">tempos alterados depois desta exportação</span>' : ''}
        <a class="text-link" href="/api/projects/${p.id}/clips/${id}/download?format=${encodeURIComponent(f)}">Baixar MP4</a>
        ${r.srt_file ? `<a class="text-link" href="/api/projects/${p.id}/clips/${id}/subtitles?format=${encodeURIComponent(f)}">Baixar SRT</a>` : ''}
        ${r.package_file ? `<a class="text-link" href="/api/projects/${p.id}/clips/${id}/publication?format=${encodeURIComponent(f)}">Pacote de publicação</a>` : ''}
        ${r.shortened ? `<div class="hint">✂ ${esc(r.shortened.removed_seconds)} s de pausas removidos</div>` : ''}
        ${r.output ? `<div class="hint">📁 Copiado para a pasta de saída: ${esc(r.output.folder)}</div>` : ''}
        ${(r.warnings || []).map(w => `<div class="hint">⚠ ${esc(w)}</div>`).join('')}</li>`;
    }).join('')}</ul>` : '';
  }
}

$('#video').addEventListener('timeupdate', () => {
  const v = $('#video');
  if (state.previewEnd !== null && v.currentTime >= state.previewEnd) { v.pause(); state.previewEnd = null; }
});

/* ------------------------------------------------------------------ início */
loadLibrary();
const saved = new URLSearchParams(location.search).get('projeto');
Promise.allSettled([loadHealth(), loadSettings()]).finally(() => { if (saved) openProject(saved); });
setInterval(() => { if (state.settings?.watch?.enabled) loadLibrary(); }, 15000);

/* ------------------------------------------------------------------ automação (v2.2) */
const AP_LABEL = { aguardando_analise: 'aguardando análise', executando: 'exportando', pausado_direitos: 'pausado (direitos)',
  sem_cortes: 'nenhum corte nos critérios', concluido: 'concluído', concluido_com_falhas: 'concluído com falhas', cancelado: 'cancelado' };

async function loadSettings() {
  try {
    const s = await api('/api/settings'); state.settings = s;
    const form = $('#form'); const up = s.upload_defaults;
    for (const key of ['target_seconds', 'desired_count', 'source_rights', 'external_audio', 'graphics_rights', 'third_party_footage', 'monetization', 'license_notes']) {
      const el = form.elements[key]; if (el && !el.dataset.touched) el.value = up[key];
    }
    const music = form.elements.recognize_music_opt_in; if (music && !music.dataset.touched) music.checked = !!up.recognize_music_opt_in;
    const ap = $('#autopilotToggle'); if (!ap.dataset.touched) ap.checked = !!s.autopilot.enabled;
    renderWatchChip();
  } catch (e) { /* configurações são opcionais para o uso manual */ }
}
for (const el of $('#form').elements) el.addEventListener('change', () => { el.dataset.touched = '1'; });

function renderWatchChip() {
  const chip = $('#sysStatus'); if (!chip || !state.settings) return;
  chip.querySelector('.watch-flag')?.remove();
  if (state.settings.watch.enabled) chip.insertAdjacentHTML('beforeend', '<span class="watch-flag"> · PASTA ✓</span>');
}

function fillSettingsForm(s) {
  const up = s.upload_defaults; const ap = s.autopilot; const nr = ap.narration;
  for (const key of ['source_rights', 'external_audio', 'graphics_rights', 'third_party_footage', 'monetization', 'target_seconds', 'desired_count', 'license_notes']) $('#s_' + key).value = up[key];
  $('#s_ap_enabled').checked = ap.enabled; $('#s_ap_max').value = ap.max_clips; $('#s_ap_min').value = ap.min_score;
  for (const box of document.querySelectorAll('.s_fmt')) box.checked = ap.formats.includes(box.value);
  $('#s_ap_framing').value = ap.framing; $('#s_ap_subtitles').checked = ap.subtitles; $('#s_ap_normalize').checked = ap.normalize_audio;
  $('#s_ap_pauses').checked = ap.shorten_pauses; $('#s_ap_temporal').checked = ap.include_temporal;
  $('#s_ap_substyle').value = ap.subtitle_style || 'classica'; $('#s_ap_hook').checked = !!ap.hook;
  const cu = s.curation || { provider: 'heuristica', consent: false, prompt: '' }; const cs = state.health?.curation || {};
  $('#s_cu_provider').value = cu.provider; $('#s_cu_consent').checked = cu.consent; $('#s_cu_prompt').value = cu.prompt || '';
  const cuHint = () => { const v = $('#s_cu_provider').value; const info = cs[v];
    $('#s_cu_hint').textContent = v === 'heuristica' ? 'Nada é enviado para fora do computador.'
      : info?.configured ? `${info.label} configurada (modelo ${info.model}).`
      : `${info?.label || v} não configurada: defina ${v === 'openai' ? 'OPENAI_API_KEY' : 'ANTHROPIC_API_KEY'} e reinicie. Sem a chave, a análise usa a heurística.`; };
  $('#s_cu_provider').onchange = cuHint; cuHint();
  const voices = state.health?.narration?.voices || [nr.voice];
  $('#s_nr_voice').innerHTML = voices.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
  $('#s_nr_enabled').checked = nr.enabled; $('#s_nr_style').value = nr.style; $('#s_nr_voice').value = nr.voice;
  $('#s_nr_volume').value = String([100, 60, 30, 0].reduce((a, b) => Math.abs(b - nr.original_volume) < Math.abs(a - nr.original_volume) ? b : a));
  $('#s_nr_subsrc').value = nr.subtitle_source; $('#s_nr_frames').checked = nr.use_frames; $('#s_nr_fallback').checked = nr.fallback_without_narration;
  $('#s_nr_consent').checked = nr.consent;
  $('#s_openai_hint').textContent = state.health?.openai_configured ? `OpenAI configurada (roteiro: ${state.health.narration.model}; voz: ${state.health.narration.tts_model}).`
    : 'OpenAI não configurada: defina OPENAI_API_KEY e reinicie. Sem ela, o piloto exporta sem narração.';
  $('#s_watch_enabled').checked = s.watch.enabled; $('#s_watch_autopilot').checked = s.watch.autopilot; $('#s_output_enabled').checked = s.output.enabled;
  $('#s_watch_dir').textContent = s.paths.watch_dir; $('#s_output_dir').textContent = s.paths.output_dir;
  const w = state.health?.automation?.watch;
  $('#s_watch_status').textContent = w ? `Importados: ${w.imported} · Falhas: ${w.failed} · Duplicados: ${w.duplicates}${w.last_error ? ' · Último erro: ' + w.last_error : ''}` : '';
}
$('#openSettings').onclick = async () => {
  await Promise.allSettled([loadHealth(), loadSettings()]);
  if (!state.settings) { notify('Não foi possível carregar as configurações.', true); return; }
  fillSettingsForm(state.settings); $('#settingsDialog').showModal();
};
$('#closeSettings').onclick = $('#cancelSettings').onclick = () => $('#settingsDialog').close();
$('#settingsForm').addEventListener('submit', async event => {
  event.preventDefault();
  const changes = {
    upload_defaults: { source_rights: $('#s_source_rights').value, external_audio: $('#s_external_audio').value,
      graphics_rights: $('#s_graphics_rights').value, third_party_footage: $('#s_third_party_footage').value,
      monetization: $('#s_monetization').value, target_seconds: Number($('#s_target_seconds').value),
      desired_count: Number($('#s_desired_count').value), license_notes: $('#s_license_notes').value },
    autopilot: { enabled: $('#s_ap_enabled').checked, max_clips: Number($('#s_ap_max').value), min_score: Number($('#s_ap_min').value),
      formats: [...document.querySelectorAll('.s_fmt')].filter(b => b.checked).map(b => b.value), framing: $('#s_ap_framing').value,
      subtitles: $('#s_ap_subtitles').checked, normalize_audio: $('#s_ap_normalize').checked, shorten_pauses: $('#s_ap_pauses').checked,
      include_temporal: $('#s_ap_temporal').checked, subtitle_style: $('#s_ap_substyle').value, hook: $('#s_ap_hook').checked,
      narration: { enabled: $('#s_nr_enabled').checked, style: $('#s_nr_style').value, voice: $('#s_nr_voice').value,
        original_volume: Number($('#s_nr_volume').value), subtitle_source: $('#s_nr_subsrc').value, use_frames: $('#s_nr_frames').checked,
        fallback_without_narration: $('#s_nr_fallback').checked, consent: $('#s_nr_consent').checked } },
    curation: { provider: $('#s_cu_provider').value, consent: $('#s_cu_consent').checked, prompt: $('#s_cu_prompt').value },
    watch: { enabled: $('#s_watch_enabled').checked, autopilot: $('#s_watch_autopilot').checked },
    output: { enabled: $('#s_output_enabled').checked },
  };
  try {
    state.settings = await api('/api/settings', { method: 'PUT', json: changes });
    for (const el of $('#form').elements) delete el.dataset.touched;
    await loadSettings(); $('#settingsDialog').close(); notify('Configurações salvas.');
  } catch (e) { notify(e.message, true); }
});

function renderAutopilot(p) {
  const panel = $('#autopilotPanel'); const ap = p.autopilot;
  const canRun = p.status === 'ready' && (!ap || ['concluido', 'concluido_com_falhas', 'sem_cortes', 'cancelado'].includes(ap.status));
  panel.hidden = !ap && !canRun;
  if (panel.hidden) return;
  $('#apStatus').textContent = ap ? (ap.status_label || AP_LABEL[ap.status] || ap.status) : 'Não usado neste projeto';
  const cfg = ap?.config;
  $('#apSummary').textContent = cfg ? `Perfil: até ${cfg.max_clips} corte(s), nota ≥ ${cfg.min_score}, formatos ${cfg.formats.join(', ')}${cfg.narration.enabled ? ', narração ' + cfg.narration.style : ''}${cfg.shorten_pauses ? ', pausas encurtadas' : ''}.`
    : 'Exporte manualmente abaixo ou rode o piloto com o perfil atual das Configurações.';
  $('#apEvents').innerHTML = (ap?.events || []).slice(-8).reverse().map(e => `<li class="${esc(e.level)}"><span class="muted">${new Date(e.at * 1000).toLocaleTimeString('pt-BR')}</span> ${esc(e.message)}</li>`).join('');
  $('#apResume').hidden = ap?.status !== 'pausado_direitos';
  $('#apRun').hidden = !canRun;
}
$('#apResume').onclick = async () => {
  if (!$('#ackRights').checked) { notify('Leia os alertas de direitos e marque a confirmação antes de continuar.', true); $('#ackRights').focus(); return; }
  try { await api(`/api/projects/${state.id}/autopilot/resume`, { method: 'POST', json: { acknowledge_rights: true } }); notify('Piloto automático retomado.'); schedule(300); }
  catch (e) { notify(e.message, true); }
};
$('#apRun').onclick = async () => {
  try { await api(`/api/projects/${state.id}/autopilot/run`, { method: 'POST' }); notify('Piloto automático iniciado.'); schedule(300); }
  catch (e) { notify(e.message, true); }
};
$('#exportAll').onclick = async () => {
  const p = state.project; if (!p) return;
  const formats = [...document.querySelectorAll('.ea-fmt')].filter(b => b.checked).map(b => b.value);
  if (!formats.length) { notify('Escolha ao menos um formato.', true); return; }
  if (p.rights?.requires_acknowledgement && !$('#ackRights').checked) { notify('Há riscos relevantes de direitos. Marque a confirmação antes de exportar.', true); $('#ackRights').focus(); return; }
  try {
    const out = await api(`/api/projects/${p.id}/export-all`, { method: 'POST', json: { formats, framing: $('#eaFraming').value,
      subtitles: $('#eaSubs').checked, subtitle_style: $('#eaSubStyle').value, hook: $('#eaHook').checked, shorten_pauses: $('#eaPauses').checked, acknowledge_rights: $('#ackRights').checked } });
    notify(`${out.jobs.length} exportação(ões) na fila${out.errors.length ? `; ${out.errors.length} não enfileirada(s): ${out.errors[0].error}` : ''}.`, !!out.errors.length);
    schedule(300);
  } catch (e) { notify(e.message, true); }
};

/* Central de automação: recursos e falhas reais, sem habilitar consentimentos. */
let automationTimer;
let importTimer;
let automationData;
const JOB_LABEL = {download:'Importação', analysis:'Análise', narration:'Roteiro IA', export:'Exportação', filmcraft:'FilmCraft'};
const JOB_STATUS = {queued:'Na fila', running:'Processando', cancelling:'Cancelando', done:'Concluído', failed:'Falhou', interrupted:'Interrompido', cancelled:'Cancelado'};
$('#sourceMode').onchange = () => {
  const link = $('#sourceMode').value === 'link';
  $('#linkEntry').hidden = !link; $('#dropzone').hidden = link;
  $('#videoUrl').disabled = !link; $('#videoUrl').required = link;
  $('#submit').textContent = link ? 'Importar link e iniciar →' : 'Analisar e sugerir cortes →';
};
$('#setupAI').onclick = () => $('#openSettings').click();
$('#refreshAutomation').onclick = () => refreshAutomation();
$('#goToAutomation').onclick = () => $('#automationTitle').scrollIntoView({behavior:'smooth'});
$('#automationPreset').onchange = () => {
  $('#presetDescription').textContent = automationData?.presets?.[$('#automationPreset').value]?.description || '';
};
$('#applyPreset').onclick = async () => {
  const button = $('#applyPreset'); button.disabled = true;
  try {
    await api(`/api/automation/presets/${$('#automationPreset').value}`, {method:'POST'});
    for (const el of $('#form').elements) delete el.dataset.touched;
    await loadSettings(); await refreshAutomation();
    notify('Perfil aplicado aos próximos vídeos. Chaves e consentimentos não foram alterados.');
  } catch (e) { notify(e.message, true); } finally {button.disabled = false;}
};
async function refreshAutomation() {
  clearTimeout(automationTimer);
  try {
    automationData = await api('/api/automation');
    const data = automationData;
    $('#automationCounts').innerHTML = [['Projetos',data.counts.projects],['Em andamento',data.counts.active],['Tarefas com falha',data.counts.attention],['Revisão de direitos',data.counts.paused]].map(([label,n]) => `<div><strong>${esc(n)}</strong><span>${esc(label)}</span></div>`).join('');
    $('#automationReadiness').innerHTML = data.readiness.map(r => `<div class="readiness-card ${r.ready ? 'ready' : 'pending'}"><strong><span aria-hidden="true">${r.ready ? '●' : '○'}</span> ${esc(r.name)}</strong><span>${r.ready ? 'Disponível / ativado' : 'Configuração pendente'}</span><small>${esc(r.detail)}</small></div>`).join('');
    $('#automationQueue').innerHTML = data.jobs.length ? data.jobs.slice(0,20).map(j => {
      const active = ['queued','running','cancelling'].includes(j.status);
      const retry = ['failed','interrupted','cancelled'].includes(j.status) && ['download','analysis'].includes(j.kind);
      const canOpen = j.kind !== 'download' || j.status === 'done';
      return `<article class="queue-item"><div><strong>${esc(JOB_LABEL[j.kind] || j.kind)} · ${esc(j.project_name)}</strong><p>${esc(j.error || j.message || JOB_STATUS[j.status])}</p><small>${esc(JOB_STATUS[j.status] || j.status)}${active ? ` · ${Math.round((j.progress||0)*100)}%` : ''}</small>${active ? `<progress max="100" value="${Math.round((j.progress||0)*100)}"></progress>` : ''}</div><div class="queue-actions">${canOpen ? `<button class="tertiary" data-open="${esc(j.project_id)}">Abrir projeto</button>` : ''}${active ? `<button class="tertiary" data-cancel="${esc(j.id)}">Cancelar</button>` : ''}${retry ? `<button class="secondary" data-retry="${esc(j.id)}">Tentar novamente</button>` : ''}</div></article>`;
    }).join('') : '<div class="queue-empty">Sua fila está livre. Importe um vídeo para começar.</div>';
  } catch (e) { $('#automationQueue').textContent = 'Não foi possível atualizar a fila. ' + e.message; }
  automationTimer = setTimeout(refreshAutomation, 3500);
}
$('#automationQueue').onclick = async event => {
  const button = event.target.closest('button'); if (!button) return;
  if (button.dataset.open) {openProject(button.dataset.open); return;}
  button.disabled = true;
  try {
    if (button.dataset.cancel) await api(`/api/jobs/${button.dataset.cancel}/cancel`, {method:'POST'});
    if (button.dataset.retry) {
      const job = await api(`/api/jobs/${button.dataset.retry}/retry`, {method:'POST'});
      if (job.kind === 'download') {localStorage.setItem('autocortes-import',job.id); watchImport(job.id);}
    }
    await refreshAutomation(); if (state.id) schedule(500);
  } catch(e) {notify(e.message,true); button.disabled=false;}
};
async function watchImport(id) {
  clearTimeout(importTimer);
  if (localStorage.getItem('autocortes-import') !== id) return;
  try {
    const job=await api(`/api/jobs/${id}`);
    if (job.status === 'done') {
      localStorage.removeItem('autocortes-import');
      if (job.result?.project_id) {openProject(job.result.project_id); notify('Vídeo importado. A análise está na fila.');}
      return;
    }
    if (['failed','cancelled','interrupted'].includes(job.status)) {
      localStorage.removeItem('autocortes-import'); notify(job.error || 'Importação interrompida.',true); return;
    }
  } catch(e) {notify(e.message,true);}
  importTimer=setTimeout(()=>watchImport(id),2000);
}
function renderTimeline(p) {
  const host=$('#clipTimeline');
  const key=JSON.stringify([p.id,(p.clips||[]).map(c=>[c.id,c.start,c.end,c.title,c.score])]);
  if(host.dataset.key===key) return;
  host.dataset.key=key;
  host.innerHTML=(p.clips||[]).map(c=>`<button type="button" class="timeline-cut" data-clip="${c.id}"><small>CORTE ${String(c.id).padStart(2,'0')} · ${c.score == null ? 'Temporal' : 'Nota '+esc(c.score)}</small><strong>${esc(c.title)}</strong><span>${fmtTime(c.start)} — ${fmtTime(c.end)}</span></button>`).join('');
}
$('#clipTimeline').onclick=event=>{
  const button=event.target.closest('[data-clip]'); if(!button) return;
  const clip=state.project?.clips.find(c=>c.id===Number(button.dataset.clip)); if(!clip) return;
  const video=$('#video'); state.previewEnd=clip.end; video.currentTime=clip.start;
  video.play().catch(()=>{}); video.scrollIntoView({behavior:'smooth',block:'center'});
};
$('#bundleDownload').onclick=event=>{
  const p=state.project;
  if(!p || !(p.clips||[]).some(c=>Object.keys(c.exports||{}).length)) {event.preventDefault(); notify('Exporte ao menos um corte para montar o pacote.',true);}
  else if((p.jobs||[]).some(j=>['queued','running','cancelling'].includes(j.status))) {event.preventDefault(); notify('Aguarde as tarefas para baixar o pacote completo.',true);}
};
refreshAutomation();
const pendingImport=localStorage.getItem('autocortes-import');
if(pendingImport && /^[a-f0-9]{32}$/.test(pendingImport)) watchImport(pendingImport);
function renderAdvanced(p) {
  const jobs=(p.jobs||[]).filter(j=>j.kind==='filmcraft');
  $('#createAdvanced').disabled=(p.jobs||[]).some(j=>['queued','running','cancelling'].includes(j.status));
  $('#advancedResults').innerHTML=jobs.slice(0,3).map(j=>j.status==='done'
    ? `<div class="row-gap"><a class="text-link" href="/api/projects/${p.id}/advanced/${j.id}/project" download>Projeto .fcproj ↓</a><a class="text-link" href="/api/projects/${p.id}/advanced/${j.id}/render" download>Vídeo da montagem ↓</a><a class="text-link" href="/api/projects/${p.id}/advanced/${j.id}/subtitles" download>SRT ↓</a></div>`
      + (j.result?.pause_removal ? `<p class="hint">Pausas removidas: ${esc(j.result.pause_removal.removed)} (${esc(j.result.pause_removal.seconds)} s). Duração: ${esc(j.result.pause_removal.duration_before)} s → ${esc(j.result.pause_removal.duration_after)} s.</p>` : '')
    : `<p class="hint">${esc(j.error||j.message||j.status)}</p>`).join('');
}
$('#createAdvanced').onclick=async()=>{
  if(!state.id) return;
  $('#createAdvanced').disabled=true;
  try {
    await api(`/api/projects/${state.id}/advanced`,{method:'POST',json:{acknowledge_rights:$('#ackRights').checked,remove_pauses:$('#advPauses').checked}});
    notify('Montagem FilmCraft na fila.'); schedule(200); refreshAutomation();
  } catch(e) {notify(e.message,true); $('#createAdvanced').disabled=false;}
};
