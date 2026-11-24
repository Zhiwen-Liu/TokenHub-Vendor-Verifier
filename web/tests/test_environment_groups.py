import json
from pathlib import Path
import threading
import time

import pytest
from web.tests.auth_helpers import AuthenticatedClient as TestClient, create_app

from web import cleanup, engine
from web.environment import Environment, dependency_stamp, dependency_requirements
from web.tests.test_preflight import HEADERS, SPEC, wait_job


def test_environment_and_data_checks_are_independent_and_persist(tmp_path, monkeypatch):
    env = Environment(tmp_path, threading.RLock(), lambda: False)
    calls = []
    monkeypatch.setattr(env, 'core_ready', lambda: calls.append('dependencies') or True)
    monkeypatch.setattr(engine, 'readiness', lambda: {'ready': True})
    monkeypatch.setattr(env, 'probe', lambda *args: calls.append('data') or {'ready': True, 'detail': 'fixture'})
    env.start(SPEC)
    assert wait_job(env)['ready']
    first = env.groups['environment']['id']
    assert calls == ['dependencies']
    env.start(SPEC, scope='data')
    assert wait_job(env)['ready']
    assert calls == ['dependencies', 'data']
    assert env.groups['environment']['id'] == first
    assert env.validated(SPEC)
    env.groups['environment']['finished'] = time.time() - 86400
    env.persist()
    restored = Environment(tmp_path, threading.RLock(), lambda: False)
    assert restored.validated(SPEC)
    assert restored.readiness({**SPEC, 'datasets': ['gpqa_diamond']}) == {'environment': True, 'data': False}
    assert not restored.readiness({**SPEC, 'datasets': ['tau2_bench']})['environment']
    monkeypatch.setattr('web.environment.dependency_stamp', lambda: 'changed-installation')
    assert not restored.validated(SPEC)


def test_environment_api_works_without_selecting_data_and_data_does_not_check_packages(tmp_path, monkeypatch):
    app = create_app(tmp_path, run_jobs=False)
    env = app.state.environment
    monkeypatch.setattr(env, 'core_ready', lambda: True)
    monkeypatch.setattr(engine, 'readiness', lambda: {'ready': True})
    monkeypatch.setattr(env, 'probe', lambda *args: {'ready': True, 'detail': 'fixture'})
    with TestClient(app, headers=HEADERS) as client:
        assert client.post('/api/environment/check', json={'mode': 'perf', 'scope': 'environment'}).status_code == 202
        assert wait_job(env)['ready']
        monkeypatch.setattr(env, 'core_ready', lambda: pytest.fail('must not check dependencies'))
        assert client.post('/api/environment/check', json={**SPEC, 'scope': 'data'}).status_code == 202
        assert wait_job(env)['ready']
        assert client.post('/api/environment/readiness', json=SPEC).json() == {'environment': True, 'data': True}


@pytest.mark.parametrize('scope,spec', [
    ('environment', SPEC), ('data', SPEC),
    ('data', {'mode': 'perf', 'model': 'test-model', 'options': {'bucket': '1k'}}),
])
def test_check_only_reports_missing_resources_without_preparing(tmp_path, monkeypatch, scope, spec):
    app = create_app(tmp_path, run_jobs=False)
    env = app.state.environment
    monkeypatch.setattr(env, 'core_ready', lambda: False)
    monkeypatch.setattr(engine, 'readiness', lambda: {'ready': True})
    monkeypatch.setattr(engine, 'missing_perf_data', lambda spec: ['1k'])
    monkeypatch.setattr(env, 'command', lambda *args, **kwargs: pytest.fail('check must not install or download'))
    def probe(kind, name, options, prepare=False):
        assert not prepare, 'check must never trigger preparation'
        return {'ready': False, 'detail': 'missing fixture'}
    monkeypatch.setattr(env, 'probe', probe)
    with TestClient(app, headers=HEADERS) as client:
        assert client.post('/api/environment/check', json={**spec, 'scope': scope}).status_code == 202
        result = wait_job(env)
        assert result['status'] == 'completed' and not result['ready']
        assert '未执行安装或下载' in result['message']
        assert all(i.get('disposition') != 'prepared' for i in result['items'])


def test_environment_signature_changes_when_installed_metadata_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, 'ROOT', tmp_path)
    monkeypatch.delenv('THVV_ENGINE_PYTHON', raising=False)
    before = dependency_stamp()
    metadata = tmp_path / '.venv-engine/lib/python3.13/site-packages/fixture-1.dist-info/METADATA'
    metadata.parent.mkdir(parents=True)
    metadata.write_text('Version: 1')
    assert before != dependency_stamp()
    before = dependency_stamp()
    metadata.write_text('Version: 22')
    assert before != dependency_stamp()


def test_perf_does_not_inherit_previous_eval_dependency_selection():
    assert dependency_requirements({'mode': 'perf', 'datasets': ['tau2_bench', 'swe_bench_pro']}) == {'core'}


def test_prepare_reuses_valid_environment_without_checking_or_installing(tmp_path, monkeypatch):
    env = Environment(tmp_path, threading.RLock(), lambda: False)
    monkeypatch.setattr(env, 'core_ready', lambda: True)
    env.start(SPEC)
    original = wait_job(env)
    monkeypatch.setattr(env, 'core_ready', lambda: pytest.fail('valid environment must be reused'))
    monkeypatch.setattr(env, 'command', lambda *args, **kwargs: pytest.fail('must not reinstall'))
    reused = env.start(SPEC, prepare=True)
    assert reused['status'] == 'completed' and reused['ready']
    assert reused['id'] != original['id'] and reused['reused_check']
    assert '直接复用' in reused['message']
    assert reused['items'][0]['disposition'] == 'reused'
    monkeypatch.setattr('web.environment.dependency_stamp', lambda: 'changed')
    checks = []
    monkeypatch.setattr(env, 'core_ready', lambda: checks.append('checked') or True)
    monkeypatch.setattr(env, 'command', lambda *args, **kwargs: True)
    env.start(SPEC, prepare=True)
    assert wait_job(env)['ready'] and checks == ['checked']


@pytest.mark.parametrize('download_ok', [True, False])
def test_prepare_reports_reused_and_downloaded_data_separately(tmp_path, monkeypatch, download_ok):
    env = Environment(tmp_path, threading.RLock(), lambda: False)
    monkeypatch.setattr(engine, 'readiness', lambda: {'ready': True})
    monkeypatch.setattr(env, 'core_ready', lambda: pytest.fail('must not check dependencies'))
    calls = []
    def probe(kind, name, options, prepare=False):
        calls.append((name, prepare))
        return {'ready': name == 'arxivmath' or (prepare and download_ok), 'detail': 'fixture'}
    monkeypatch.setattr(env, 'probe', probe)
    env.start({**SPEC, 'datasets': ['arxivmath', 'mmlu_pro']}, prepare=True, scope='data')
    result = wait_job(env)
    assert calls == [('arxivmath', False), ('mmlu_pro', False), ('mmlu_pro', True)]
    assert result['items'][0]['disposition'] == 'reused'
    assert result['items'][1]['disposition'] == ('prepared' if download_ok else None)
    assert result['ready'] is download_ok
    assert '复用已有 1 项' in result['message']
    assert f'本次准备 {int(download_ok)} 项' in result['message']


def test_confirmed_environment_cleanup_preserves_objects_and_history(tmp_path, monkeypatch):
    app = create_app(tmp_path / 'state', run_jobs=False)
    catalog = engine.registry()
    monkeypatch.setattr(engine, 'registry', lambda: catalog)
    monkeypatch.setattr(engine, 'ROOT', tmp_path)
    monkeypatch.delenv('THVV_ENGINE_PYTHON', raising=False)
    target = tmp_path / '.venv-engine'
    target.mkdir()
    (target / 'fixture').write_text('dependency')
    web_env = tmp_path / '.venv-web'
    web_env.mkdir()
    with TestClient(app, headers=HEADERS) as client:
        obj = client.post('/api/objects', json={'name': 'keep', 'url': 'https://example.com/v1', 'api_key': 'keep-secret'}).json()
        store = app.state.store
        store.execute('INSERT INTO tasks(id,payload,secrets,status,created) VALUES(?,?,?,?,?)',
                      ('keep-task', json.dumps({'object': obj, 'spec': SPEC}), store.encrypt({}), 'succeeded', '2026-11-24'))
        plan = client.post('/api/environment/cleanup-preview', json={'mode': 'perf', 'scope': 'environment'}).json()
        assert target.exists() and plan['items']
        assert client.post('/api/environment/cleanup', json={'plan_id': 'unknown'}).status_code == 422
        assert target.exists()
        assert client.post('/api/environment/cleanup', json={'plan_id': plan['id']}).status_code == 202
        assert wait_job(app.state.environment)['status'] == 'completed'
        assert not target.exists() and web_env.exists()
        assert client.get('/api/objects').json()[0]['id'] == obj['id']
        assert client.get('/api/tasks/keep-task').status_code == 200
        assert client.post('/api/environment/cleanup', json={'plan_id': plan['id']}).status_code == 422


def test_cleanup_blocks_active_tasks_and_preserves_changed_preview(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, 'ROOT', tmp_path)
    path = tmp_path / 'thvv/eval/datasets/datasets'
    path.mkdir(parents=True)
    (path / 'data.arrow').write_text('fixture')
    active = [True]
    env = Environment(tmp_path / 'state', threading.RLock(), lambda: active[0])
    with pytest.raises(ValueError, match='等待'):
        env.preview_cleanup('data', SPEC)
    active[0] = False
    plan = env.preview_cleanup('data', SPEC)
    active[0] = True
    with pytest.raises(ValueError, match='等待'):
        env.clean(plan['id'])
    active[0] = False
    (path / 'new.arrow').write_text('new content after preview')
    env.clean(plan['id'])
    assert wait_job(env)['status'] == 'failed'
    assert (path / 'new.arrow').exists()
    plan = env.preview_cleanup('data', SPEC)
    env.clean(plan['id'])
    assert wait_job(env)['status'] == 'completed'
    assert not path.exists()


def test_cleanup_rejects_external_environments_and_symlinks(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, 'ROOT', tmp_path)
    monkeypatch.setenv('THVV_ENGINE_PYTHON', '/external/python')
    with pytest.raises(ValueError, match='外部'):
        cleanup.inventory('environment', SPEC, [])
    monkeypatch.delenv('THVV_ENGINE_PYTHON')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'keep').write_text('keep')
    (tmp_path / '.venv-engine').symlink_to(outside, target_is_directory=True)
    assert cleanup.inventory('environment', SPEC, [])['entries'] == []
    assert (outside / 'keep').exists()


def test_tokenizer_cleanup_skips_model_weights_and_unselected_repos(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, 'ROOT', tmp_path / 'repo')
    monkeypatch.setenv('MODELSCOPE_CACHE', str(tmp_path / 'ms'))
    monkeypatch.setenv('HF_HUB_CACHE', str(tmp_path / 'hf'))
    repo = tmp_path / 'ms/models/org--model/snapshots/master'
    repo.mkdir(parents=True)
    (repo / 'tokenizer.json').write_text('{}')
    (repo / 'model.safetensors').write_text('weights')
    plan = cleanup.inventory('data', SPEC, ['org/model'])
    assert not plan['entries'] and any('模型权重' in note for note in plan['notes'])
    (repo / 'model.safetensors').unlink()
    plan = cleanup.inventory('data', SPEC, ['org/model'])
    other = tmp_path / 'ms/models/org--other'
    other.mkdir()
    cleanup.execute(plan['entries'])
    assert not repo.exists() and other.exists()


def test_cleanup_restores_only_unmodified_lfs_files(tmp_path, monkeypatch):
    import hashlib
    monkeypatch.setattr(engine, 'ROOT', tmp_path)
    content = b'fixture data\n' * 100
    pointer = ('version https://git-lfs.github.com/spec/v1\noid sha256:' + hashlib.sha256(content).hexdigest() + '\nsize ' + str(len(content)) + '\n').encode()
    monkeypatch.setattr(cleanup.subprocess, 'check_output', lambda *args, **kwargs: pointer)
    path = tmp_path / 'thvv/perf/datasets_clean/perf_zh_1k.jsonl'
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    spec = {'mode': 'perf', 'options': {'bucket': '1k'}}
    plan = cleanup.inventory('data', spec, [])
    assert len(plan['entries']) == 1
    cleanup.execute(plan['entries'])
    assert path.read_bytes() == pointer
    path.write_bytes(content + b'local change')
    plan = cleanup.inventory('data', spec, [])
    assert plan['entries'] == []
    assert path.read_bytes().endswith(b'local change')
