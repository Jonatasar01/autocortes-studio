"""Regressões de conteúdo duplicado e resultados por execução do piloto."""
from unittest.mock import Mock
import threading
import pytest

from app import autopilot, config, service, settings, storage, watcher
from app.jobs import JobContext, JobManager, Handler


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path / 'data')
    pid, folder = storage.new_project_dir()
    cfg = settings.defaults()['autopilot']
    cfg['formats'] = ['9:16']
    storage.write_project(pid, {'id': pid, 'name': 'Teste', 'status': 'ready', 'filename': 'source.mp4',
                               'autopilot': {'status': 'executando', 'run_id': 'new', 'config': cfg,
                                             'selected': [1, 2], 'events': [], 'handled_narrations': []}})
    manager = Mock()
    manager.for_project.return_value = []
    monkeypatch.setattr(service, '_jobs', lambda: manager)
    return pid, folder, manager


def job(pid, kind, status, clip=1, run='new'):
    return {'id': f'{run}-{kind}-{clip}', 'project_id': pid, 'kind': kind, 'status': status,
            'params': {'autopilot': True, 'autopilot_run_id': run, 'clip_id': clip, 'format': '9:16'}}


def status(pid):
    return storage.read_project(pid)['autopilot']['status']


@pytest.mark.parametrize('size,offset', [(1024*1024+100, 1024*1024+10),
                                        (4*1024*1024, 2*1024*1024)])
def test_fingerprint_covers_tail_and_middle(tmp_path, size, offset):
    a, b = tmp_path/'a.mp4', tmp_path/'b.mp4'
    content = bytearray(b'a' * size)
    a.write_bytes(content)
    content[offset] = ord('b')
    b.write_bytes(content)
    assert watcher.fingerprint(a) != watcher.fingerprint(b)
    b.write_bytes(a.read_bytes())
    assert watcher.fingerprint(a) == watcher.fingerprint(b)


def test_legacy_fingerprint_recomputed_from_saved_source(project):
    pid, folder, _ = project
    source = folder/'source.mp4'
    source.write_bytes(b'original')
    with storage.edit_project(pid) as p:
        p['source_fingerprint'] = 'old-partial-hash'
    digest = watcher.fingerprint(source)
    assert watcher._duplicate(digest, source.stat().st_size)['id'] == pid
    assert storage.read_project(pid)['source_fingerprint'] == digest


@pytest.mark.parametrize('old_status', ['failed', 'cancelled', 'running'])
def test_previous_run_does_not_affect_new_run(project, old_status):
    pid, _, manager = project
    manager.for_project.return_value = [job(pid, 'export', old_status, run='old'),
                                        job(pid, 'export', 'done'), job(pid, 'export', 'done', clip=2)]
    autopilot.refresh(pid)
    assert status(pid) == 'concluido'


def test_failed_script_is_not_success(project):
    pid, _, manager = project
    script = job(pid, 'export', 'failed')
    manager.for_project.return_value = [script, job(pid, 'export', 'done', clip=2)]
    autopilot.after_job('export', JobContext(manager, script), 'failed', 'teste')
    assert status(pid) == 'concluido_com_falhas'


def test_only_script_failed_is_not_reported_as_cancelled(project):
    pid, _, manager = project
    script = job(pid, 'export', 'failed')
    manager.for_project.return_value = [script]
    autopilot.after_job('export', JobContext(manager, script), 'failed', 'teste')
    assert status(pid) == 'concluido_com_falhas'






def test_dispatch_pending_does_not_finish_run(project):
    pid, _, manager = project
    with storage.edit_project(pid) as p:
        p['autopilot']['dispatching'] = True
    manager.for_project.return_value = [job(pid, 'export', 'done')]
    autopilot.refresh(pid)
    assert status(pid) == 'executando'


def test_missing_planned_export_is_not_success(project):
    pid, _, manager = project
    manager.for_project.return_value = [job(pid, 'export', 'done')]
    autopilot.refresh(pid)
    assert status(pid) == 'concluido_com_falhas'


def test_late_callback_cannot_enqueue_for_new_run(project, monkeypatch):
    pid, _, manager = project
    submit = Mock()
    monkeypatch.setattr(autopilot, 'submit_exports', submit)
    script = job(pid, 'export', 'done', run='old')
    autopilot.after_job('export', JobContext(manager, script), 'done', None)
    submit.assert_not_called()
    assert status(pid) == 'executando'


def test_restart_replays_callback_without_reexporting_completed_files(project, monkeypatch):
    pid, _, manager = project
    script = job(pid, 'export', 'done')
    manager.for_project.return_value = [script, job(pid, 'export', 'done'), job(pid, 'export', 'done', clip=2)]
    submit = Mock()
    monkeypatch.setattr(service, 'request_export', submit)
    autopilot.recover(pid)
    submit.assert_not_called()
    assert status(pid) == 'concluido'
    assert status(pid) == 'concluido'


def test_cancel_callback_does_not_hold_database_lock(tmp_path):
    read_completed = threading.Event()
    observed = []
    threads = []
    def callback(ctx, status, error):
        def read():
            manager.get(ctx.id)
            read_completed.set()
        thread = threading.Thread(target=read)
        threads.append(thread)
        thread.start()
        observed.append(read_completed.wait(2))
    manager = JobManager(tmp_path/'jobs.db', {'export': Handler(lambda ctx: None, on_end=callback)})
    try:
        queued, _ = manager.submit('test', 'export', {})
        assert manager.cancel(queued['id'])['status'] == 'cancelled'
        for thread in threads:
            thread.join(3)
        assert observed == [True]
    finally:
        manager.stop()
