"""Fila local persistente (SQLite, sem dependências externas).

Recursos: estados claros, progresso real, cancelamento, limite de tarefas simultâneas por tipo,
prevenção de trabalhos duplicados (dedupe_key), recuperação após encerramento inesperado
(tarefas em execução viram ``interrupted`` e podem ser refeitas) e registro de eventos por etapa.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .errors import AppError, Cancelled, sanitize

log = logging.getLogger('autocortes.jobs')
ACTIVE = ('queued', 'running', 'cancelling')
TERMINAL = ('done', 'failed', 'cancelled', 'interrupted')

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT NOT NULL, params TEXT NOT NULL,
    status TEXT NOT NULL, stage TEXT, progress REAL DEFAULT 0, message TEXT, error TEXT, result TEXT,
    dedupe_key TEXT, created REAL NOT NULL, started REAL, finished REAL
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, kind, created);
CREATE INDEX IF NOT EXISTS jobs_project ON jobs(project_id, created);
CREATE TABLE IF NOT EXISTS job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, ts REAL NOT NULL,
    stage TEXT, level TEXT, message TEXT
);
CREATE INDEX IF NOT EXISTS events_job ON job_events(job_id, id);
"""


@dataclass
class Handler:
    run: Callable[['JobContext'], dict | None]
    workers: int = 1
    on_end: Callable[['JobContext', str, str | None], None] | None = None
    retry_codes: set[str] | frozenset[str] = frozenset()   # erros transitórios (rede, limite de uso)
    max_retries: int = 0


class JobContext:
    def __init__(self, manager: 'JobManager', job: dict):
        self.manager = manager
        self.id = job['id']
        self.project_id = job['project_id']
        self.kind = job['kind']
        self.params = job['params']
        self.cancel_event = threading.Event()
        self.stage_name: str | None = None
        self._last_write = 0.0
        self._fraction = 0.0

    def check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise Cancelled()

    def stage(self, name: str, message: str) -> None:
        self.check_cancel()
        self.stage_name = name
        self.manager._update(self.id, stage=name, message=message, progress=round(self._fraction, 4))
        self.manager.event(self.id, name, 'info', message)

    def progress(self, fraction: float, message: str | None = None) -> None:
        self._fraction = max(self._fraction, max(0.0, min(1.0, fraction)))
        now = time.monotonic()
        if now - self._last_write >= 0.7 or fraction >= 1.0:
            self._last_write = now
            fields = {'progress': round(self._fraction, 4)}
            if message:
                fields['message'] = message
            self.manager._update(self.id, **fields)

    def span(self, low: float, high: float) -> Callable[[float, str], None]:
        """Converte o progresso de uma etapa (0–1) para a faixa global [low, high]."""
        return lambda p, msg='': self.progress(low + (high - low) * max(0.0, min(1.0, p)), msg or None)

    def warn(self, message: str) -> None:
        self.manager.event(self.id, self.stage_name, 'warning', message)


class JobManager:
    def __init__(self, db_path: Path, handlers: dict[str, Handler]):
        self.db_path = db_path
        self.handlers = handlers
        self._db_lock = threading.RLock()
        self._wake = threading.Condition()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._running: dict[str, JobContext] = {}
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None, timeout=30)
        self._conn.row_factory = sqlite3.Row
        with self._db_lock:
            self._conn.execute('PRAGMA journal_mode=WAL')
            self._conn.executescript(SCHEMA)
            columns = {r['name'] for r in self._conn.execute('PRAGMA table_info(jobs)').fetchall()}
            if 'attempts' not in columns:  # migração de bancos da v2.1
                self._conn.execute('ALTER TABLE jobs ADD COLUMN attempts INTEGER DEFAULT 0')
            if 'not_before' not in columns:
                self._conn.execute('ALTER TABLE jobs ADD COLUMN not_before REAL DEFAULT 0')

    # ------------------------------------------------------------------ banco
    def _row(self, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        job = dict(row)
        job['params'] = json.loads(job['params'] or '{}')
        job['result'] = json.loads(job['result']) if job.get('result') else None
        return job

    def _update(self, job_id: str, **fields) -> None:
        if not fields:
            return
        cols = ', '.join(f'{k}=?' for k in fields)
        with self._db_lock:
            self._conn.execute(f'UPDATE jobs SET {cols} WHERE id=?', (*fields.values(), job_id))

    def event(self, job_id: str, stage: str | None, level: str, message: str) -> None:
        with self._db_lock:
            self._conn.execute('INSERT INTO job_events(job_id, ts, stage, level, message) VALUES (?,?,?,?,?)',
                               (job_id, time.time(), stage, level, sanitize(message, 1000)))

    def get(self, job_id: str) -> dict | None:
        with self._db_lock:
            return self._row(self._conn.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())

    def events(self, job_id: str, limit: int = 50) -> list[dict]:
        with self._db_lock:
            rows = self._conn.execute('SELECT ts, stage, level, message FROM job_events WHERE job_id=? '
                                      'ORDER BY id DESC LIMIT ?', (job_id, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def for_project(self, project_id: str, active_only: bool = False, limit: int = 100) -> list[dict]:
        sql = 'SELECT * FROM jobs WHERE project_id=?'
        if active_only:
            sql += " AND status IN ('queued','running','cancelling')"
        sql += ' ORDER BY created DESC LIMIT ?'
        with self._db_lock:
            return [self._row(r) for r in self._conn.execute(sql, (project_id, limit)).fetchall()]

    def recent(self, limit=40):
        with self._db_lock:
            return [self._row(r) for r in self._conn.execute(
                "SELECT * FROM jobs ORDER BY CASE WHEN status IN ('queued','running','cancelling') THEN 0 ELSE 1 END, created DESC LIMIT ?",
                (min(200, max(1, limit)),)).fetchall()]

    # ------------------------------------------------------------------ ciclo de vida
    def start(self) -> list[dict]:
        """Liga os trabalhadores; devolve as tarefas que estavam rodando e viraram ``interrupted``."""
        with self._db_lock:
            stale = [self._row(r) for r in self._conn.execute(
                "SELECT * FROM jobs WHERE status IN ('running','cancelling')").fetchall()]
            self._conn.execute("UPDATE jobs SET status='interrupted', finished=?, "
                               "error='Interrompido: o aplicativo foi encerrado durante o processamento.' "
                               "WHERE status IN ('running','cancelling')", (time.time(),))
        for job in stale:
            handler = self.handlers.get(job['kind'])
            if handler and handler.on_end:
                try:
                    handler.on_end(JobContext(self, job), 'interrupted',
                                   'Interrompido: o aplicativo foi encerrado durante o processamento.')
                except Exception:  # recuperação nunca impede a inicialização
                    log.exception('Falha ao recuperar tarefa %s', job['id'])
        for kind, handler in self.handlers.items():
            for n in range(handler.workers):
                thread = threading.Thread(target=self._worker, args=(kind,), name=f'job-{kind}-{n}', daemon=True)
                thread.start()
                self._threads.append(thread)
        return stale

    def stop(self, timeout: float = 10) -> None:
        self._stop.set()
        for ctx in list(self._running.values()):
            ctx.cancel_event.set()
        with self._wake:
            self._wake.notify_all()
        for thread in self._threads:
            thread.join(timeout=timeout)
        self._threads.clear()
        with self._db_lock:
            self._conn.close()

    # ------------------------------------------------------------------ operações
    def submit(self, project_id: str, kind: str, params: dict, dedupe_key: str | None = None) -> tuple[dict, bool]:
        """Retorna (tarefa, criada). Se já existir tarefa ativa igual, devolve a existente."""
        if kind not in self.handlers:
            raise AppError('Tipo de tarefa inválido.', 400)
        with self._db_lock:
            if dedupe_key:
                row = self._conn.execute("SELECT * FROM jobs WHERE dedupe_key=? AND status IN "
                                         "('queued','running','cancelling') ORDER BY created DESC LIMIT 1",
                                         (dedupe_key,)).fetchone()
                if row:
                    return self._row(row), False
            job_id = uuid.uuid4().hex
            self._conn.execute('INSERT INTO jobs(id, project_id, kind, params, status, stage, progress, message, '
                               'dedupe_key, created) VALUES (?,?,?,?,?,?,?,?,?,?)',
                               (job_id, project_id, kind, json.dumps(params, ensure_ascii=False), 'queued',
                                'fila', 0.0, 'Aguardando na fila', dedupe_key, time.time()))
        with self._wake:
            self._wake.notify_all()
        return self.get(job_id), True

    def cancel(self, job_id: str) -> dict | None:
        queued = False
        with self._db_lock:
            job = self.get(job_id)
            if not job or job['status'] not in ACTIVE:
                return job
            if job['status'] == 'queued':
                self._conn.execute("UPDATE jobs SET status='cancelled', finished=?, message='Cancelado antes de iniciar' "
                                   "WHERE id=? AND status='queued'", (time.time(), job_id))
                queued = True
            else:
                self._conn.execute("UPDATE jobs SET status='cancelling', message='Cancelando…' WHERE id=?", (job_id,))
        # Callbacks podem obter locks de projeto; nunca executá-los sob o lock do banco.
        if queued:
            handler = self.handlers.get(job['kind'])
            if handler and handler.on_end:
                try:
                    handler.on_end(JobContext(self, job), 'cancelled', None)
                except Exception:
                    log.exception('Falha no encerramento da tarefa %s', job_id)
            return self.get(job_id)
        ctx = self._running.get(job_id)
        if ctx:
            ctx.cancel_event.set()
        return self.get(job_id)

    def wait(self, job_id: str, timeout: float = 120) -> dict | None:
        """Auxiliar para testes e scripts."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            job = self.get(job_id)
            if job and job['status'] in TERMINAL:
                return job
            time.sleep(0.1)
        return self.get(job_id)

    # ------------------------------------------------------------------ execução
    def _claim(self, kind: str) -> dict | None:
        with self._db_lock:
            row = self._conn.execute("SELECT id FROM jobs WHERE status='queued' AND kind=? "
                                     "AND COALESCE(not_before, 0) <= ? ORDER BY created LIMIT 1",
                                     (kind, time.time())).fetchone()
            if not row:
                return None
            self._conn.execute("UPDATE jobs SET status='running', started=?, message='Iniciando' WHERE id=?",
                               (time.time(), row['id']))
            return self.get(row['id'])

    def _worker(self, kind: str) -> None:
        handler = self.handlers[kind]
        while not self._stop.is_set():
            job = self._claim(kind)
            if job is None:
                with self._wake:
                    self._wake.wait(timeout=1.0)
                continue
            ctx = JobContext(self, job)
            self._running[job['id']] = ctx
            status, error, result = 'done', None, None
            retry_code = None
            try:
                result = handler.run(ctx)
            except Cancelled:
                status = 'cancelled'
            except AppError as exc:
                status, error = 'failed', exc.message
                retry_code = exc.code
                tech = exc.details.get('technical') if exc.details else None
                if tech:
                    self.event(job['id'], ctx.stage_name, 'error', 'Detalhe técnico: ' + tech)
            except Exception as exc:  # erro inesperado: registra sem expor caminhos/segredos
                status, error = 'failed', 'Erro inesperado: ' + sanitize(exc, 300)
                log.error('Tarefa %s falhou:\n%s', job['id'], sanitize(traceback.format_exc(), 4000))
            finally:
                self._running.pop(job['id'], None)
            attempts = int(job.get('attempts') or 0)
            if (status == 'failed' and retry_code in handler.retry_codes and attempts < handler.max_retries
                    and not self._stop.is_set() and self.get(job['id'])['status'] == 'running'):
                from . import config
                delay = config.JOB_RETRY_BASE_SECONDS * (2 ** attempts)
                self.event(job['id'], ctx.stage_name, 'warning',
                           f'{error} Nova tentativa automática em {delay} s ({attempts + 1}/{handler.max_retries}).')
                self._update(job['id'], status='queued', attempts=attempts + 1, not_before=time.time() + delay,
                             message=f'Nova tentativa em {delay} s', progress=0.0, stage='fila')
                with self._wake:
                    self._wake.notify_all()
                continue
            # Se o cancelamento chegou depois da conclusão, prevalece o resultado concluído e validado.
            fields = {'status': status, 'finished': time.time(),
                      'message': {'done': 'Concluído', 'cancelled': 'Cancelado pelo usuário',
                                  'failed': 'Falhou'}[status]}
            if status == 'done':
                fields['progress'] = 1.0
                fields['result'] = json.dumps(result or {}, ensure_ascii=False)
            if error:
                fields['error'] = error
                self.event(job['id'], ctx.stage_name, 'error', error)
            self._update(job['id'], **fields)
            if handler.on_end:
                try:
                    handler.on_end(ctx, status, error)
                except Exception:
                    log.exception('Falha no encerramento da tarefa %s', job['id'])
