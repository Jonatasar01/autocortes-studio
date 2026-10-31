'use strict';
// AutoCortes Studio — Configurações e recursos deste computador.
// Scripts clássicos carregados em ordem por studio.html: compartilham as variáveis globais do studio.js.

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
  syncYoutubeSettings();
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
    await saveYoutubeKeys();
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
    autopilot: 'false',
  };
}

function uploadFile(f, p, authRetried = false) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/projects?' + new URLSearchParams(p));
    xhr.setRequestHeader('X-AutoCortes', '1');
    if (sessionAuthorization) xhr.setRequestHeader('Authorization', sessionAuthorization);
    xhr.setRequestHeader('X-Filename', encodeURIComponent(f.name));
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    $('uploadProgress').hidden = false;
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      $('uploadBar').value = Math.round(100 * e.loaded / e.total);
      $('uploadPercent').textContent = $('uploadBar').value + '%';
    };
    xhr.onload = async () => {
      let d = {};
      try { d = JSON.parse(xhr.responseText); } catch { /* resposta vazia */ }
      if (xhr.status === 401 && d.code === 'auth_required') {
        sessionAuthorization = '';
        if (!authRetried && await requestAuthentication()) {
          uploadFile(f, p, true).then(resolve, reject);
          return;
        }
      }
      if (xhr.status < 200 || xhr.status >= 300) reject(responseError(xhr.status, d));
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
  if (editMode() === 'advanced') {
    store.set('pref.target', String(clamp(Number($('target').value) || 35, 10, 90)));
    setMode('advanced');   // quem escolhe "Avançado" ao criar quer as ferramentas completas no editor
  }
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
