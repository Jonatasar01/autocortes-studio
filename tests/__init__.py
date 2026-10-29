"""Isola as pastas e as chaves durante os testes (nada é lido ou escrito em data/, entrada/ ou saida/ reais)."""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix='autocortes-tests-')
# Sempre (não setdefault): data/ real guarda a chave do Gemini colada na tela (secrets.json). Um teste que lesse a
# pasta real chamaria a API do Google com a chave do usuário e a mostraria na saída do teste.
os.environ['AUTOCORTES_DATA'] = os.path.join(_TMP, 'data')
os.environ['GEMINI_API_KEY'] = ''
os.environ['GOOGLE_API_KEY'] = ''
os.environ.setdefault('AUTOCORTES_WATCH_DIR', os.path.join(_TMP, 'entrada'))
os.environ.setdefault('AUTOCORTES_OUTPUT_DIR', os.path.join(_TMP, 'saida'))
# Legenda precisa baixa e carrega um modelo grande na GPU; os testes a simulam (test_refine_subtitles).
os.environ.setdefault('WHISPER_REFINE_MODEL', 'off')
# O Ollama é o provedor padrão da curadoria: sem isto, os testes chamariam o modelo real instalado na máquina.
# Os testes da curadoria local o simulam; o teste real opcional (AUTOCORTES_TEST_REAL_OLLAMA=1) desliga a trava.
os.environ.setdefault('AUTOCORTES_DISABLE_OLLAMA', '1')
