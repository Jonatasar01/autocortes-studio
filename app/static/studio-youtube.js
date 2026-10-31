'use strict';
// AutoCortes Studio — Publicar no YouTube.
// Scripts clássicos carregados em ordem por studio.html: compartilham as variáveis globais do studio.js.

// ------------------------------------------------------------------ publicar no YouTube
async function openYoutube(clipId, format) {
  const p = state.project, c = p?.clips.find((x) => x.id === clipId);
  if (!c) return;
  try {
    const st = await api('/api/youtube/status');
    if (!st.connected) {
      toast('Conecte a conta do YouTube em Configurações para publicar.', true);
      return openSettings();
    }
  } catch (e) { return toast(e.message, true); }
  const rec = c.exports[format] || {};
  $('ytClip').textContent = clipLabel(c.id) + ' · ' + format + (rec.validation?.duration ? ' · ' + fmt(rec.validation.duration) : '');
  $('ytVideoTitle').value = (rec.title || c.title || '').slice(0, 100);
  $('ytDescription').value = (c.description || '').slice(0, 300);
  $('ytPrivacy').value = 'private';
  $('ytRightsRow').hidden = !p.rights?.requires_acknowledgement;
  $('ytRights').checked = false;
  $('ytForm').dataset.clip = c.id;
  $('ytForm').dataset.format = format;
  $('ytPublishAt').value = '';
  $('ytThumb').hidden = true;
  $('ytDialog').showModal();
  syncThumb();
}
// Capa automática: gerada no servidor a partir do vídeo exportado; a imagem é só a prévia.
function syncThumb() {
  const p = state.project, f = $('ytForm').dataset, img = $('ytThumb');
  if (!$('ytThumbOn').checked || !p) { img.hidden = true; return; }
  img.onload = () => { img.hidden = false; };
  img.onerror = () => { img.hidden = true; };
  img.src = '/api/projects/' + p.id + '/clips/' + f.clip + '/thumbnail?format=' + encodeURIComponent(f.format) +
    '&t=' + Date.now();
}
$('ytThumbOn').addEventListener('change', syncThumb);
$('ytSuggest').onclick = async () => {
  const p = state.project, f = $('ytForm').dataset;
  $('ytSuggest').disabled = true;
  $('ytSuggest').textContent = 'Escrevendo…';
  try {
    const s = await api('/api/projects/' + p.id + '/clips/' + f.clip + '/suggest-text?format=' +
      encodeURIComponent(f.format), 'POST');
    $('ytVideoTitle').value = s.title;
    $('ytDescription').value = [s.description, (s.hashtags || []).join(' ')].filter(Boolean).join('\n\n');
    toast(s.warning || (s.provider ? 'Texto escrito pela IA (' + s.provider + '). Revise antes de enviar.'
      : 'Sem IA disponível: sugestão feita pelas palavras do corte.'), !!s.warning);
  } catch (e) { toast(e.message, true); }
  finally { $('ytSuggest').disabled = false; $('ytSuggest').textContent = 'Escrever com IA'; }
};
$('ytForm').onsubmit = async (e) => {
  e.preventDefault();
  const p = state.project, f = $('ytForm').dataset;
  if (!$('ytRightsRow').hidden && !$('ytRights').checked) return toast('Confirme que leu os alertas de direitos.', true);
  $('ytSend').disabled = true;
  try {
    const job = await api('/api/projects/' + p.id + '/clips/' + f.clip + '/youtube', 'POST', {
      format: f.format, title: $('ytVideoTitle').value.trim(), description: $('ytDescription').value,
      privacy: $('ytPrivacy').value, acknowledge_rights: $('ytRights').checked,
      publish_at: $('ytPublishAt').value ? new Date($('ytPublishAt').value).toISOString() : null,
      thumbnail: $('ytThumbOn').checked});
    $('ytDialog').close();
    p.jobs = [job, ...(p.jobs || []).filter((j) => j.id !== job.id)];
    renderExports();
    toast('Enviando para o YouTube… acompanhe em Exportações.');
  } catch (err) { toast(err.message, true); } finally { $('ytSend').disabled = false; }
};
async function syncYoutubeSettings() {
  try {
    const st = await api('/api/youtube/status');
    $('ytStatus').textContent = st.connected
      ? 'Conta conectada: envie os cortes exportados pelo botão de envio em Exportações.'
      : st.configured ? 'Cliente OAuth salvo' + (st.key_hint ? ' (ID termina em …' + st.key_hint + ')' : '') + '. Falta conectar a conta.'
        : 'Para publicar direto no YouTube, cole o ID e a chave do cliente OAuth e conecte a conta.';
    $('ytDisconnect').hidden = !st.connected;
    $('ytResultsOpen').hidden = !st.connected;
    $('ytConnect').textContent = st.connected ? 'Conectar outra conta' : 'Conectar conta do YouTube';
  } catch { $('ytStatus').textContent = 'Status do YouTube indisponível.'; }
}
async function saveYoutubeKeys() {
  const id = $('ytClientId').value.trim(), secret = $('ytClientSecret').value.trim();
  if (id) await api('/api/settings/keys/youtube_id', 'PUT', {key: id});
  if (secret) await api('/api/settings/keys/youtube_secret', 'PUT', {key: secret});
  $('ytClientId').value = $('ytClientSecret').value = '';
}
$('ytConnect').onclick = async () => {
  try {
    await saveYoutubeKeys();
    const {url} = await api('/api/youtube/connect', 'POST');
    window.open(url, '_blank', 'noopener');
    toast('Entre na sua conta na página do Google; depois volte aqui.');
    const until = Date.now() + 5 * 60 * 1000;
    const poll = async () => {
      const st = await api('/api/youtube/status').catch(() => null);
      if (st?.connected) { syncYoutubeSettings(); return toast('YouTube conectado.'); }
      if (Date.now() < until) setTimeout(poll, 3000);
    };
    setTimeout(poll, 3000);
  } catch (e) { toast(e.message, true); }
};
$('ytResultsOpen').onclick = async () => {
  const dlg = $('ytResultsDialog'), table = $('ytResultsTable');
  $('ytResultsNote').textContent = 'Carregando os números do YouTube…';
  table.hidden = true;
  dlg.showModal();
  try {
    const {videos} = await api('/api/youtube/results');
    const n = (v) => v === null || v === undefined ? '—' : v.toLocaleString('pt-BR');
    const situation = (v) => v.publish_at && v.privacy === 'private'
      ? 'Agendado para ' + new Date(v.publish_at).toLocaleString('pt-BR', {dateStyle: 'short', timeStyle: 'short'})
      : ({public: 'Público', unlisted: 'Não listado', private: 'Privado'}[v.privacy] || 'Removido ou indisponível');
    table.tBodies[0].innerHTML = videos.map((v) => '<tr><td><a href="' + escape(v.url) + '" target="_blank" rel="noopener">' +
      escape(v.title || 'Corte ' + v.clip_id) + '</a></td><td>' + escape(v.project || '') + '</td><td class="num">' +
      n(v.views) + '</td><td class="num">' + n(v.likes) + '</td><td class="num">' + n(v.comments) + '</td><td>' +
      escape(situation(v)) + '</td></tr>').join('');
    table.hidden = !videos.length;
    $('ytResultsNote').textContent = videos.length
      ? videos.length + ' corte(s) publicados pelo AutoCortes, do mais visto ao menos visto.'
      : 'Nenhum corte publicado pelo AutoCortes ainda. Publique pelo botão de envio em Exportações.';
  } catch (e) { $('ytResultsNote').textContent = e.message; }
};
$('ytDisconnect').onclick = async () => {
  try { await api('/api/youtube', 'DELETE'); syncYoutubeSettings(); toast('YouTube desconectado.'); }
  catch (e) { toast(e.message, true); }
};

// Excluir exportações concluídas (uma ou todas do projeto). As cópias na pasta de saída ficam.
async function deleteExports(items) {
  const p = state.project;
  if (!p) return;
  const all = p.clips.flatMap((c) => Object.keys(c.exports || {}).map((format) => ({clip: c.id, format})));
  items = items || all;
  if (!items.length) return;
  const one = items.length === 1;
  const ok = await askConfirm({
    title: one ? 'Excluir exportação' : 'Excluir exportações', danger: true,
    yes: one ? 'Excluir' : 'Excluir ' + items.length,
    text: (one ? 'O arquivo de ' + clipLabel(items[0].clip) + ' (' + items[0].format + ') será apagado do projeto. '
      : 'Os ' + items.length + ' arquivos exportados deste projeto serão apagados. ') +
      'Os cortes continuam e podem ser exportados de novo. As cópias já salvas na pasta de saída não são apagadas.',
  });
  if (!ok) return;
  let freed = 0, failed = 0;
  for (const it of items) {
    try {
      freed += (await api('/api/projects/' + p.id + '/clips/' + it.clip + '/exports?format=' +
        encodeURIComponent(it.format), 'DELETE')).freed_bytes;
      const c = p.clips.find((x) => x.id === it.clip);
      if (c?.exports) delete c.exports[it.format];
    } catch (err) { failed++; if (one) toast(err.message, true); }
  }
  renderExports();
  render();
  if (!one || !failed) toast((items.length - failed) + (items.length - failed === 1 ? ' exportação excluída' : ' exportações excluídas') +
    (freed ? ' · ' + cacheSize(freed) + ' liberados' : '') + (failed ? ' · ' + failed + ' não puderam ser excluídas' : '') + '.', failed > 0);
  loadCacheUsage();
}
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
