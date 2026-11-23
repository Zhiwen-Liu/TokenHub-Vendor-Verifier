import json
from pathlib import Path
import sys
import tarfile
import time
import zipfile

from fastapi.testclient import TestClient
import pytest

from web.app import create_app, publish_artifacts, redact
from web import connectivity, engine
from web.environment import Environment

HEADERS = {'X-THVV-Request': '1'}


@pytest.fixture
def preflight_passes(monkeypatch):
    monkeypatch.setattr(Environment, 'validated', lambda self, spec: True)
    monkeypatch.setattr(connectivity, 'check', lambda *args: {'ready': True, 'message': 'fixture success'})


@pytest.fixture
def client(tmp_path, monkeypatch, preflight_passes):
    monkeypatch.setattr(engine, 'readiness', lambda: {'ready': True, 'python': sys.executable})
    app = create_app(tmp_path, run_jobs=False)
    with TestClient(app, headers=HEADERS) as c:
        yield c, app.state.store


def add_object(c, key='secret-original-123'):
    r = c.post('/api/objects', json={'name': '端点 A', 'url': 'http://localhost:9900/v1', 'api_key': key})
    assert r.status_code == 201, r.text
    assert key not in r.text if key else True
    return r.json()


def spec(obj):
    return {'object_id': obj['id'], 'model': 'test/model', 'mode': 'eval', 'protocol': 'openai', 'datasets': ['arxivmath'], 'options': {}}


def test_objects_do_not_require_model_and_secrets_never_return(client):
    c, store = client
    obj = add_object(c)
    assert obj['has_api_key'] is True
    assert store.rows('SELECT secret FROM objects')[0]['secret'] != 'secret-original-123'
    assert 'secret-original-123' not in c.get('/api/objects').text
    r = c.patch('/api/objects/' + obj['id'], json={'name': '编辑端点', 'url': 'http://localhost:9900/v1'})
    assert r.status_code == 200 and r.json()['has_api_key']
    r = c.patch('/api/objects/' + obj['id'], json={'name': '编辑端点', 'url': 'http://localhost:9900/v1', 'api_key': ''})
    assert r.status_code == 200 and not r.json()['has_api_key']
    assert c.delete('/api/objects/' + obj['id']).status_code == 200
    assert c.get('/api/objects').json() == []


def test_replay_retains_original_endpoint_secret_and_parameters(client):
    c, store = client
    obj = add_object(c)
    request = spec(obj)
    request['options'] = {'limit': '5', 'repeats': '2'}
    original = c.post('/api/tasks', json=request).json()
    assert original['status'] == 'queued'
    assert 'secrets' not in original
    c.patch('/api/objects/' + obj['id'], json={'name': '修改后的对象', 'url': 'https://other.example.com/v1', 'api_key': 'different-secret'})
    c.delete('/api/objects/' + obj['id'])
    replay = c.post('/api/tasks/' + original['id'] + '/replay', json=request)
    assert replay.status_code == 201, replay.text
    replay = replay.json()
    assert replay['id'] != original['id']
    assert replay['object']['url'] == obj['url']
    assert replay['spec']['options'] == request['options']
    assert store.task(replay['id'], private=True)['secrets']['api_key'] == 'secret-original-123'
    assert 'secret-original-123' not in c.get('/api/tasks').text
    assert c.delete('/api/tasks/' + original['id']).status_code == 409
    store.execute("UPDATE tasks SET status='succeeded' WHERE id=?", (original['id'],))
    assert c.delete('/api/tasks/' + original['id']).status_code == 200
    assert c.get('/api/tasks/' + original['id']).status_code == 404


def test_defaults_registry_and_protocol_validation(client):
    c, _ = client
    meta = c.get('/api/meta').json()
    assert len(meta['datasets']) == 18
    assert meta['datasets']['arxivmath']['default_eval_batch_size'] == 15
    assert meta['datasets']['gpqa_diamond']['default_repeats'] == 3
    obj = add_object(c, '')
    request = spec(obj)
    request['options'] = {'limit': '', 'repeats': ''}
    assert c.post('/api/tasks', json=request).json()['spec']['options'] == {}
    request['all'] = True
    request['judge_api_key'] = 'judge-fixture-key'
    created = c.post('/api/tasks', json=request).json()
    assert len(created['spec']['datasets']) == 15
    request['protocol'] = 'anthropic'
    assert c.post('/api/tasks', json=request).status_code == 422
    request['protocol'] = 'openai'; request['model'] = '../../escape'
    assert c.post('/api/tasks', json=request).status_code == 422


def test_invalid_and_browser_cross_site_requests_do_not_leak_keys(client):
    c, _ = client
    assert c.post('/api/objects', headers={'X-THVV-Request': ''}, json={'name': 'x', 'url': 'http://localhost'}).status_code == 403
    assert c.get('/api/objects', headers={'Host': 'evil.example'}).status_code == 400
    bad = c.post('/api/objects', json={'name': 'x', 'url': 'http://localhost', 'api_key': ['sensitive-test-value']})
    assert bad.status_code == 422 and 'sensitive-test-value' not in bad.text
    bad = c.post('/api/objects', json={'name': 'x', 'url': 'https://example.com/v1?api_key=sensitive-test-value'})
    assert bad.status_code == 422 and 'sensitive-test-value' not in bad.text


def test_native_command_mapping_and_workspace_isolation(tmp_path):
    task = {'object': {'url': 'http://localhost:9900/v1'}, 'spec': {'mode': 'perf', 'model': 'test', 'protocol': 'anthropic', 'options': {'bucket': '1k', 'number': '20', 'parallel': '3', 'warmup': '0'}}}
    cmd, env = engine.prepare_run(task, tmp_path / 'work', {'api_key': ''})
    assert cmd[-4:] == ['bench', '1k', '20', '3']
    assert env['API_URL'] == 'http://localhost:9900/v1/messages'
    assert env['WARMUP_NUM'] == '0'
    assert env['THVV_ALLOW_EMPTY_API_KEY'] == '1'
    assert not (tmp_path / 'work/configs/.env').exists()
    assert (tmp_path / 'work/perf/run.sh').is_file()
    task['spec'] = {'mode': 'eval', 'model': 'test', 'protocol': 'openai', 'datasets': ['arxivmath'], 'options': {}}
    cmd, env = engine.prepare_run(task, tmp_path / 'second', {'api_key': 'private-key'})
    assert '--limit' not in cmd and '--repeats' not in cmd
    assert 'private-key' not in ' '.join(cmd)
    assert env['API_URL'] == 'http://localhost:9900/v1'


def test_archive_and_excel_are_sanitized(tmp_path):
    assert 'private&amp;secret' not in redact('key=private&amp;secret', {'api_key': 'private&secret'})
    source = tmp_path / 'work/eval/results/run'
    source.mkdir(parents=True)
    (source / 'eval_summary.json').write_text('{"api_key":"sensitive-key"}')
    with zipfile.ZipFile(source / 'report.xlsx', 'w') as archive:
        archive.writestr('xl/sharedStrings.xml', '<t>sensitive-key</t>')
    publish_artifacts(tmp_path / 'work', tmp_path / 'artifacts', {'api_key': 'sensitive-key'})
    with tarfile.open(tmp_path / 'artifacts/results.tar.gz') as archive:
        summary = archive.extractfile('eval/run/eval_summary.json').read()
        assert b'sensitive-key' not in summary
    with zipfile.ZipFile(tmp_path / 'artifacts/eval/run/report.xlsx') as archive:
        assert b'sensitive-key' not in archive.read('xl/sharedStrings.xml')


def test_background_process_to_results_and_history(tmp_path, monkeypatch, preflight_passes):
    monkeypatch.setattr(engine, 'readiness', lambda: {'ready': True})
    def fake_native(task, workspace, secrets):
        workspace.mkdir(parents=True)
        script = """
import json, pathlib
p=pathlib.Path('eval/results/run/arxivmath');p.mkdir(parents=True)
(p.parent/'all_eval_summary.json').write_text(json.dumps({'results':[{'dataset':'arxivmath','success':True,'score':0.5,'num_samples':2,'skipped_count':0}]}))
(p/'report.html').write_text('<h1>report</h1><script>alert(1)</script>')
(p/'configs.yaml').write_text('api_key: sensitive-fixture-key')
print('fixture completed sensitive-fixture-key',flush=True)
"""
        return [sys.executable, '-c', script], {}
    monkeypatch.setattr(engine, 'prepare_run', fake_native)
    app = create_app(tmp_path, run_jobs=True)
    with TestClient(app, headers=HEADERS) as c:
        obj = add_object(c, 'sensitive-fixture-key')
        task = c.post('/api/tasks', json=spec(obj)).json()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            detail = c.get('/api/tasks/' + task['id']).json()
            if detail['status'] not in ('queued', 'running'):
                break
            time.sleep(.05)
        assert detail['status'] == 'succeeded', detail
        assert detail['eval_results'][0]['score'] == .5
        assert 'sensitive-fixture-key' not in c.get('/api/tasks/' + task['id'] + '/logs').text
        assert not (tmp_path / 'tasks' / task['id'] / 'work').exists()
        html = next(a['path'] for a in detail['artifacts'] if a['path'].endswith('.html'))
        response = c.get('/api/tasks/' + task['id'] + '/artifacts/' + html)
        assert response.status_code == 200 and 'sandbox' in response.headers['content-security-policy']
        assert c.get('/api/tasks/' + task['id'] + '/artifacts/%2e%2e/%2e%2e/state.sqlite').status_code == 404
        assert c.delete('/api/tasks/' + task['id']).status_code == 200
        assert not (tmp_path / 'tasks' / task['id']).exists()
