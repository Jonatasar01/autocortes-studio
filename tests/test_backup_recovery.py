import json
from unittest.mock import patch
import pytest
from app import config, storage


@pytest.mark.parametrize('corrupt', [False, True])
def test_backup_only_is_listed_and_restored(tmp_path, monkeypatch, corrupt):
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    pid, folder = storage.new_project_dir()
    project = {'id': pid, 'name': 'Recuperável', 'status': 'ready'}
    backup = folder/'project.json.bak'
    backup.write_text(json.dumps(project), encoding='utf-8')
    if corrupt:
        (folder/'project.json').write_text('{quebrado', encoding='utf-8')
    assert storage.list_projects()[0]['id'] == pid
    assert json.loads((folder/'project.json').read_text(encoding='utf-8')) == project
    assert json.loads(backup.read_text(encoding='utf-8')) == project


def test_failure_between_replaces_remains_visible(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    pid, folder = storage.new_project_dir()
    old = {'id': pid, 'name': 'Anterior', 'status': 'ready'}
    storage.write_project(pid, old)
    real_replace = storage.os.replace
    def interrupted(source, target):
        if str(target).endswith('project.json'):
            raise OSError('interrupção simulada')
        return real_replace(source, target)
    with patch.object(storage.os, 'replace', side_effect=interrupted):
        with pytest.raises(OSError):
            storage.write_project(pid, {**old, 'name': 'Nova'})
    assert not (folder/'project.json').exists()
    assert storage.list_projects()[0]['name'] == 'Anterior'
    assert (folder/'project.json').is_file()
