"""publicar_cloudflare_windows.bat: publica pelo Cloudflare Tunnel sem expor a porta e sempre com senha."""
from __future__ import annotations

from app import config

BAT = config.ROOT / 'publicar_cloudflare_windows.bat'


def test_script_is_ascii_with_crlf_line_endings_only():
    data = BAT.read_bytes()
    data.decode('ascii')  # sem acentos: o cmd usa a página de código do console
    assert data.count(b'\n') == data.count(b'\r\n'), 'LF solto: o cmd erra o goto/rótulos em arquivos só com LF'


def test_app_stays_on_loopback_and_requires_a_password():
    text = BAT.read_text(encoding='ascii')
    assert 'set AUTOCORTES_HOST=127.0.0.1' in text  # só o cloudflared fala com a internet
    assert 'AUTOCORTES_HOST=0.0.0.0' not in text
    assert ':nopassword' in text and 'if "%AUTOCORTES_PASSWORD%"=="" goto nopassword' in text
    assert text.index('goto nopassword') < text.index('call :startapp')  # nada sobe sem senha


def test_both_tunnel_modes_allow_only_their_own_hosts():
    text = BAT.read_text(encoding='ascii')
    assert 'cloudflared tunnel --no-autoupdate --url http://127.0.0.1:%AUTOCORTES_PORT%' in text
    assert '%AUTOCORTES_ALLOWED_HOSTS%,.trycloudflare.com' in text
    assert 'cloudflared tunnel --no-autoupdate run' in text and 'TUNNEL_TOKEN=%CLOUDFLARE_TUNNEL_TOKEN%' in text
    assert '%AUTOCORTES_ALLOWED_HOSTS%,%AUTOCORTES_PUBLIC_HOST%' in text
    assert 'ALLOWED_HOSTS=*' not in text
    assert 'winget install --id Cloudflare.cloudflared' in text
