"""Gerenciamento de processos do FFmpeg: cancelamento encerra a árvore inteira.

No Windows o FFmpeg instalado por Chocolatey/Scoop é um "shim": um executável
que inicia o ffmpeg.exe real como processo filho. Encerrar só o shim deixa o
filho vivo, segurando o pipe de progresso, e o cancelamento só terminava
quando a renderização acabava. Estes testes reproduzem esse cenário com um
"ffmpeg" falso que delega para um processo neto, sem depender do FFmpeg real.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from app import media
from app.errors import Cancelled

# Processo neto: registra o próprio PID, emite progresso e fica vivo.
GRANDCHILD = (
    'import os, sys, time\n'
    'open(sys.argv[1], "w").write(str(os.getpid()))\n'
    'for i in range(600):\n'
    '    print(f"out_time_us={i * 100000}", flush=True)\n'
    '    time.sleep(0.1)\n'
)


def _alive(pid: int) -> bool:
    if os.name == 'nt':
        out = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/NH'], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:  # processo zumbi (já encerrado, aguardando coleta) não conta como vivo
        return Path(f'/proc/{pid}/status').read_text().find('State:\tZ') < 0
    except OSError:
        return True


class ShimTreeTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='autocortes-proc-'))
        self.pid_file = self.dir / 'neto.pid'
        script = self.dir / 'neto.py'
        script.write_text(GRANDCHILD, encoding='utf-8')
        if os.name == 'nt':
            shim = self.dir / 'ffmpeg-shim.bat'
            shim.write_text(f'@echo off\r\n"{sys.executable}" "{script}" "{self.pid_file}"\r\n', encoding='utf-8')
        else:
            shim = self.dir / 'ffmpeg-shim'
            # O shell espera o neto em segundo plano, como um shim que não repassa sinais.
            shim.write_text(f'#!/bin/sh\n"{sys.executable}" "{script}" "{self.pid_file}" &\nwait\n', encoding='utf-8')
            shim.chmod(0o755)
        self.shim = str(shim)

    def tearDown(self):
        pid = self._grandchild_pid(timeout=0)
        if pid and _alive(pid):  # não deixa processo órfão mesmo se o teste falhar
            if os.name == 'nt':
                subprocess.run(['taskkill', '/F', '/PID', str(pid)], capture_output=True)
            else:
                os.kill(pid, 9)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _grandchild_pid(self, timeout: float = 10.0) -> int | None:
        limit = time.monotonic() + timeout
        while True:
            try:
                text = self.pid_file.read_text().strip()
                if text:
                    return int(text)
            except (OSError, ValueError):
                pass
            if time.monotonic() >= limit:
                return None
            time.sleep(0.05)

    def _run(self, event: threading.Event, progress: list[float]):
        with mock.patch.object(media, 'require_binary', return_value=self.shim), \
             mock.patch.object(media, '_priority_prefix', return_value=[]):
            media.run_ffmpeg([], duration=60, cancel_event=event,
                             on_progress=lambda p, m: progress.append(p))

    def test_cancel_stops_whole_tree_promptly(self):
        event = threading.Event()
        progress: list[float] = []
        result: dict = {}

        def target():
            started = time.monotonic()
            try:
                self._run(event, progress)
            except BaseException as exc:  # noqa: BLE001 - o teste inspeciona a exceção
                result['error'] = exc
            result['elapsed'] = time.monotonic() - started

        worker = threading.Thread(target=target, daemon=True)
        worker.start()
        pid = self._grandchild_pid()
        self.assertIsNotNone(pid, 'o processo neto não iniciou')
        # Espera o progresso começar: o cancelamento acontece durante a execução.
        limit = time.monotonic() + 10
        while not progress and time.monotonic() < limit:
            time.sleep(0.02)
        self.assertTrue(progress, 'nenhum progresso recebido antes do cancelamento')
        cancelled_at = time.monotonic()
        event.set()
        worker.join(timeout=15)
        self.assertFalse(worker.is_alive(), 'run_ffmpeg não retornou após o cancelamento')
        self.assertIsInstance(result.get('error'), Cancelled)
        self.assertLess(time.monotonic() - cancelled_at, 6.0)
        limit = time.monotonic() + 5
        while _alive(pid) and time.monotonic() < limit:
            time.sleep(0.05)
        self.assertFalse(_alive(pid), 'o processo filho do shim continuou rodando')


class StopStrategyTests(unittest.TestCase):
    """Verifica, sem processos reais, qual estratégia de encerramento é usada."""

    def _proc(self):
        proc = mock.Mock(pid=4321)
        proc.poll.return_value = None
        return proc

    def test_windows_kills_tree_with_taskkill(self):
        proc = self._proc()
        with mock.patch.object(media.os, 'name', 'nt'), \
             mock.patch.object(media.subprocess, 'run') as run:
            media._kill_tree(proc)
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[0], 'taskkill')
        self.assertIn('/T', cmd)
        self.assertIn('/F', cmd)
        self.assertEqual(cmd[-1], '4321')

    @unittest.skipIf(os.name == 'nt', 'grupos de processos POSIX')
    def test_posix_signals_process_group(self):
        proc = self._proc()
        with mock.patch.object(media.os, 'killpg') as killpg:
            media._kill_tree(proc)
        killpg.assert_called_once()
        self.assertEqual(killpg.call_args[0][0], 4321)

    @unittest.skipIf(os.name == 'nt', 'grupos de processos POSIX')
    def test_posix_tree_already_gone_is_ignored(self):
        proc = self._proc()
        with mock.patch.object(media.os, 'killpg', side_effect=ProcessLookupError):
            media._kill_tree(proc)  # não deve levantar

    def test_spawn_isolates_process_tree(self):
        kwargs = media._tree_popen_kwargs()
        if os.name == 'nt':
            self.assertIn('creationflags', kwargs)
        else:
            self.assertTrue(kwargs.get('start_new_session'))


if __name__ == '__main__':
    unittest.main()
