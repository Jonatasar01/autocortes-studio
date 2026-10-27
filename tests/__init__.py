"""Isola as pastas de automação durante os testes (nada é escrito em entrada/ ou saida/ reais)."""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix='autocortes-tests-')
os.environ.setdefault('AUTOCORTES_WATCH_DIR', os.path.join(_TMP, 'entrada'))
os.environ.setdefault('AUTOCORTES_OUTPUT_DIR', os.path.join(_TMP, 'saida'))
# Legenda precisa baixa e carrega um modelo grande na GPU; os testes a simulam (test_refine_subtitles).
os.environ.setdefault('WHISPER_REFINE_MODEL', 'off')
