'use strict';
// AutoCortes Studio — Início: roda por último, depois que todos os arquivos do Studio foram carregados.
// Scripts clássicos carregados em ordem por studio.html: compartilham as variáveis globais do studio.js.

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
