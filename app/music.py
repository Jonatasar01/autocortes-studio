"""Reconhecimento musical externo OPT-IN via API do ACRCloud.

- Só executa com consentimento explícito do usuário E credenciais configuradas.
- Envia apenas amostras curtas (12 s, mono, 16 kHz) — nunca o vídeo nem o áudio inteiro.
- Número de consultas limitado (custo): ACRCLOUD_MAX_SAMPLES (padrão 3).
- Credenciais só por variáveis de ambiente; nunca aparecem em mensagens, relatórios ou logs.
- Correspondência é indício, não prova. Ausência de correspondência NÃO significa música livre.
- Isto não é Content ID.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from . import config
from .errors import Cancelled
from .media import require_binary

log = logging.getLogger('autocortes.music')
SAMPLE_SECONDS = 12
UNCERTAIN_SCORE = 70
STATUS_MESSAGES = {
    3001: 'Chave de acesso ACRCloud ausente ou inválida.',
    3003: 'Limite de consultas da conta ACRCloud atingido.',
    3014: 'Assinatura inválida: confira o segredo ACRCloud.',
    3015: 'Limite de consultas por segundo do ACRCloud excedido.',
    2004: 'O ACRCloud não conseguiu gerar a impressão digital da amostra.',
}


def is_configured() -> bool:
    return all(os.getenv(k, '').strip() for k in ('ACRCLOUD_HOST', 'ACRCLOUD_ACCESS_KEY', 'ACRCLOUD_ACCESS_SECRET'))


def sample_starts(duration: float, count: int) -> list[float]:
    if duration <= SAMPLE_SECONDS * 1.5 or count <= 1:
        return [0.0]
    room = duration - SAMPLE_SECONDS
    return [round(room * k / (count - 1), 2) for k in range(count)]


def recognize_music(path: Path, duration: float, opted_in: bool, has_audio: bool,
                    cancel_event: threading.Event | None = None, audio_index: int | None = None) -> dict:
    base = {'provider': 'ACRCloud', 'samples': [], 'matches': [],
            'privacy': f'Somente amostras de {SAMPLE_SECONDS} s de áudio são enviadas, e apenas com consentimento.'}
    if not opted_in:
        return {**base, 'status': 'not_requested', 'message': 'Reconhecimento externo desativado (privacidade).'}
    if not has_audio:
        return {**base, 'status': 'no_audio', 'message': 'Vídeo sem faixa de áudio.'}
    host = os.getenv('ACRCLOUD_HOST', '').strip()
    key = os.getenv('ACRCLOUD_ACCESS_KEY', '').strip()
    secret = os.getenv('ACRCLOUD_ACCESS_SECRET', '').strip()
    if not all((host, key, secret)):
        return {**base, 'status': 'not_configured',
                'message': 'ACRCloud não configurado: o reconhecimento de música NÃO foi realizado.'}
    if not re.fullmatch(r'[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+', host):
        return {**base, 'status': 'error', 'message': 'Domínio ACRCloud inválido na configuração.'}
    try:
        import requests
    except ImportError:
        return {**base, 'status': 'not_configured',
                'message': 'Biblioteca requests ausente (instale requirements-music.txt). Reconhecimento não realizado.'}
    matches: list[dict] = []
    samples: list[dict] = []
    errors: list[str] = []
    for start in sample_starts(duration, config.ACRCLOUD_MAX_SAMPLES):
        if cancel_event is not None and cancel_event.is_set():
            raise Cancelled()
        sample = {'start': start, 'seconds': SAMPLE_SECONDS, 'status': 'pending'}
        samples.append(sample)
        cmd = [require_binary('ffmpeg'), '-hide_banner', '-nostdin', '-loglevel', 'error', '-ss', f'{start:.2f}',
               '-i', str(path), '-t', str(SAMPLE_SECONDS)]
        if audio_index is not None:
            cmd += ['-map', f'0:{audio_index}']
        cmd += ['-vn', '-ac', '1', '-ar', '16000', '-c:a', 'libmp3lame', '-b:a', '64k', '-f', 'mp3', 'pipe:1']
        try:
            audio = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=90).stdout
        except (subprocess.SubprocessError, OSError):
            sample['status'] = 'extraction_failed'
            errors.append('Falha ao extrair amostra de áudio.')
            continue
        if not audio:
            sample['status'] = 'empty'
            continue
        timestamp = str(int(time.time()))
        signed = '\n'.join(['POST', '/v1/identify', key, 'audio', '1', timestamp]).encode('utf-8')
        signature = base64.b64encode(hmac.new(secret.encode('utf-8'), signed, hashlib.sha1).digest()).decode('ascii')
        fields = {'access_key': key, 'sample_bytes': str(len(audio)), 'timestamp': timestamp,
                  'signature': signature, 'data_type': 'audio', 'signature_version': '1'}
        try:
            response = requests.post('https://' + host + '/v1/identify',
                                     files={'sample': ('sample.mp3', audio, 'audio/mpeg')}, data=fields, timeout=20)
            response.raise_for_status()
            payload = response.json()
            code = int((payload.get('status') or {}).get('code', -1))
        except Exception as exc:  # rede, HTTP, JSON inválido — nunca expõe credenciais
            log.warning('ACRCloud indisponível: %s', type(exc).__name__)
            sample['status'] = 'error'
            errors.append('Serviço indisponível ou resposta inválida.')
            continue
        if code == 1001:
            sample['status'] = 'no_match'
            continue
        if code != 0:
            sample['status'] = 'error'
            errors.append(STATUS_MESSAGES.get(code, f'Erro do provedor (código {code}).'))
            if code in (3001, 3003, 3014):
                break  # credencial/limite: não adianta repetir (evita custo)
            continue
        sample['status'] = 'matched'
        for record in (payload.get('metadata') or {}).get('music', []) or []:
            title = str(record.get('title') or 'Faixa não identificada')[:150]
            artists = ', '.join(str(a.get('name', '')) for a in record.get('artists', []) or []
                                if isinstance(a, dict) and a.get('name'))[:150]
            try:
                score = int(record.get('score', 0))
            except (TypeError, ValueError):
                score = 0
            if any(m['title'] == title and m['artist'] == artists for m in matches):
                continue
            matches.append({'time': round(start, 1), 'title': title, 'artist': artists or 'Artista não informado',
                            'score': score, 'uncertain': score < UNCERTAIN_SCORE, 'provider': 'ACRCloud',
                            'warning': 'Correspondência é indício, não comprova titularidade nem infração.'})
    queried = sum(1 for s in samples if s['status'] in ('matched', 'no_match'))
    if matches:
        status, message = 'matched', 'Possíveis músicas reconhecidas; confira direitos e licenças.'
    elif queried and not errors:
        status = 'no_match'
        message = (f'Nenhuma correspondência em {queried} amostra(s) de {SAMPLE_SECONDS} s. Outras partes do áudio '
                   'não foram verificadas e músicas não reconhecidas podem ser protegidas.')
    elif queried:
        status = 'no_match'
        message = (f'Nenhuma correspondência em {queried} amostra(s); outras consultas falharam: ' + errors[0] +
                   ' Resultado incompleto.')
    else:
        status = 'error'
        message = 'Não foi possível concluir a consulta musical. ' + (errors[0] if errors else '')
    return {**base, 'status': status, 'matches': matches, 'samples': samples, 'message': message.strip(),
            'queries_used': queried + sum(1 for s in samples if s['status'] == 'error')}
