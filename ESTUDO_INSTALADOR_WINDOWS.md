# Estudo: instalador para Windows

## Situação atual
- `iniciar_windows.bat` exige **Python 3.11+ já instalado**, cria a `.venv` e instala só o `requirements.txt`
  (FastAPI/uvicorn).
- Transcrição (`faster-whisper`), rostos (`opencv-python-headless`) e as DLLs de CUDA ficam nos extras
  (`requirements-ai.txt`, `requirements-gpu.txt` e outros) e **não são instalados pelo inicializador**. Uma pessoa que
  baixa o projeto e executa o `.bat` fica sem transcrição e sem seguir rosto até instalar esses extras à mão.
- FFmpeg: só um aviso se não estiver no PATH (aqui veio do `winget`).

Tamanho medido da `.venv` desta máquina: **2,5 GB**, sendo **2,0 GB das DLLs NVIDIA** (cuBLAS/cuDNN), 110 MB de OpenCV,
63 MB de PyAV, 60 MB de CTranslate2 e 46 MB de onnxruntime. O modelo Whisper é baixado na primeira análise
(large-v3-turbo: ~1,6 GB) e fica no cache do Hugging Face.

## Opções

| Opção | Como funciona | Tamanho do download | Prós | Contras |
|---|---|---|---|---|
| **A. `.bat` atual melhorado** | Pergunta "tem placa NVIDIA?" e instala `requirements-ai` (+ `-gpu`); instala o FFmpeg pelo `winget` se faltar | ~150 MB (CPU) / ~2,1 GB (GPU), na hora | Nenhuma ferramenta nova; atualiza com `git pull` | Continua exigindo Python instalado; terminal aberto |
| **B. Instalador Inno Setup + Python embutido** | `setup.exe` com Python embeddable 3.12 (~25 MB), o código do app e um passo que roda `pip install` dos extras escolhidos; atalho no menu Iniciar | ~40 MB de instalador + pacotes baixados na instalação | Não exige Python; desinstalador; atalho; escolha CPU/GPU na instalação | Precisa de um script `.iss` e de assinar o executável para o SmartScreen não reclamar |
| **C. PyInstaller (pasta única)** | Congela Python + pacotes numa pasta | ~2,5 GB (GPU) / ~400 MB (CPU) | Tudo num pacote, sem internet na instalação | Pacote enorme por causa das DLLs CUDA; cada atualização baixa tudo de novo; antivírus costuma bloquear executáveis do PyInstaller |
| **D. MSIX / Microsoft Store** | Pacote assinado da Store | como C | Atualização automática | Exige conta de desenvolvedor e certificado; sandbox atrapalha o FFmpeg e pastas monitoradas |

## Recomendação
1. **Agora (barato): opção A.** O inicializador passa a instalar os extras de transcrição e rostos e, se houver placa
   NVIDIA (`nvidia-smi` responde), os de GPU; oferece `winget install Gyan.FFmpeg` quando o FFmpeg falta. Resolve o
   problema real: hoje a instalação "funciona" mas fica sem transcrição.
2. **Depois: opção B** para quem não tem Python. Python embutido + Inno Setup, com a escolha CPU/GPU no assistente,
   sem levar as DLLs de CUDA dentro do instalador (são baixadas só se a pessoa escolher GPU).
3. **Evitar C e D:** o tamanho das DLLs CUDA e os alarmes de antivírus pesam mais que a vantagem.

## Riscos e cuidados
- Instalar pacotes exige internet; o instalador deve mostrar o progresso e repetir quando falhar.
- O modelo Whisper continua sendo baixado na primeira análise (~1,6 GB): avisar o tamanho.
- Assinatura de código: sem certificado, o Windows mostra "Editor desconhecido" no SmartScreen.
- Licenças: o FFmpeg "full build" do Gyan é GPL; distribuir o executável junto do instalador exige acompanhar a
  licença e o código-fonte. Instalar pelo `winget` evita redistribuir.
