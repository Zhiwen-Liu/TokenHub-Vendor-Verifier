import json

from web.tests.auth_helpers import AuthenticatedClient as TestClient, create_app
import httpx
import pytest

from web import connectivity, engine
from web.environment import Environment
from web.tests.test_preflight import HEADERS, mock_http


def test_judge_check_uses_native_defaults_and_minimal_openai_request(tmp_path, monkeypatch):
    defaults = engine.judge_defaults()
    monkeypatch.setenv('JUDGE_MODEL', 'must-not-override-native-default')
    monkeypatch.setenv('JUDGE_BASE_URL', 'https://unrelated.example/v1')
    def handler(request):
        assert str(request.url) == defaults['url'] + '/chat/completions'
        assert request.headers['Authorization'] == 'Bearer judge-test-key'
        assert json.loads(request.content) == {'model': defaults['model'], 'messages': [{'role': 'user', 'content': 'Hi'}], 'max_tokens': 1, 'stream': False}
        return httpx.Response(200, json={'choices': [{'message': {'content': 'Hi'}}]})
    mock_http(monkeypatch, handler)
    with TestClient(create_app(tmp_path, run_jobs=False), headers=HEADERS) as client:
        assert client.get('/api/meta').json()['judge_defaults'] == defaults
        result = client.post('/api/judge-connectivity', json={'api_key': 'judge-test-key'})
        assert result.status_code == 200 and result.json()['ready']
        assert 'judge-test-key' not in result.text
        assert client.get('/api/objects').json() == []
        assert client.get('/api/tasks').json() == []


def test_judge_custom_config_and_error_redaction(tmp_path, monkeypatch):
    def handler(request):
        assert str(request.url) == 'https://judge.example/prefix/v1/chat/completions'
        assert json.loads(request.content)['model'] == 'my-judge'
        assert request.headers['Authorization'] == 'Bearer judge-custom-secret'
        return httpx.Response(401, json={'error': {'message': 'bad judge-custom-secret'}})
    mock_http(monkeypatch, handler)
    with TestClient(create_app(tmp_path, run_jobs=False), headers=HEADERS) as client:
        result = client.post('/api/judge-connectivity', json={'model': 'my-judge', 'url': 'https://judge.example/prefix/v1/chat/completions', 'api_key': 'judge-custom-secret'})
        assert result.status_code == 200 and not result.json()['ready']
        assert '认证失败' in result.json()['message'] and 'judge-custom-secret' not in result.text


@pytest.mark.parametrize('body', [{}, {'api_key': '  '}, {'api_key': 'key\nvalue'}, {'api_key': 'fixture', 'url': 'file:///tmp/fixture'}])
def test_invalid_judge_config_never_sends_request(tmp_path, monkeypatch, body):
    monkeypatch.setattr(connectivity, 'check', lambda *args: pytest.fail('invalid judge config must not make a request'))
    with TestClient(create_app(tmp_path, run_jobs=False), headers=HEADERS) as client:
        assert client.post('/api/judge-connectivity', json=body).status_code == 422


def test_judge_replay_uses_original_key_and_allows_override(tmp_path, monkeypatch):
    app = create_app(tmp_path, run_jobs=False)
    calls = []
    monkeypatch.setattr(connectivity, 'check', lambda *args: calls.append(args) or {'ready': True})
    with TestClient(app, headers=HEADERS) as client:
        store = app.state.store
        payload = {'object': {'id': 'deleted'}, 'spec': {'mode': 'eval', 'datasets': ['hle']}}
        store.execute('INSERT INTO tasks(id,payload,secrets,status,created) VALUES(?,?,?,?,?)',
                      ('source', json.dumps(payload), store.encrypt({'api_key': 'target-key', 'judge_api_key': 'original-judge-key'}), 'succeeded', '2026-11-24'))
        body = {'replay_id': 'source', 'model': 'replay-judge', 'url': 'https://judge.example/v1'}
        assert client.post('/api/judge-connectivity', json=body).json()['ready']
        assert calls[-1] == ('https://judge.example/v1', 'replay-judge', 'openai', 'original-judge-key')
        assert client.post('/api/judge-connectivity', json={**body, 'api_key': 'new-judge-key'}).json()['ready']
        assert calls[-1][-1] == 'new-judge-key'
        assert 'original-judge-key' not in client.get('/api/tasks').text


@pytest.mark.parametrize('selection', [{'datasets': ['hle']}, {'datasets': ['simple_qa']}, {'all': True}])
def test_task_start_rechecks_judge_and_rejects_failed_judge(tmp_path, monkeypatch, selection):
    monkeypatch.setattr(engine, 'readiness', lambda: {'ready': True})
    monkeypatch.setattr(Environment, 'validated', lambda *args: True)
    judge_ready = [True]
    calls = []
    def check(url, model, protocol, key):
        calls.append((url, model, protocol, key))
        return {'ready': judge_ready[0] if model == 'my-judge' else True, 'message': 'HTTP 401 · 认证失败'}
    monkeypatch.setattr(connectivity, 'check', check)
    app = create_app(tmp_path, run_jobs=False)
    with TestClient(app, headers=HEADERS) as client:
        obj = client.post('/api/objects', json={'name': 'target', 'url': 'https://target.example/v1', 'api_key': 'target-secret'}).json()
        body = {'object_id': obj['id'], 'model': 'target-model', 'mode': 'eval', 'options': {'judge-model': 'my-judge', 'judge-url': 'https://judge.example/v1'}, 'judge_api_key': 'judge-secret', **selection}
        assert client.post('/api/judge-connectivity', json={'model': 'my-judge', 'url': 'https://judge.example/v1', 'api_key': 'judge-secret'}).json()['ready']
        judge_ready[0] = False
        result = client.post('/api/tasks', json=body)
        assert result.status_code == 409 and '裁判模型连通性检查失败' in result.text
        assert client.get('/api/tasks').json() == []
        assert calls[-2:] == [('https://target.example/v1', 'target-model', 'openai', 'target-secret'), ('https://judge.example/v1', 'my-judge', 'openai', 'judge-secret')]
        judge_ready[0] = True
        result = client.post('/api/tasks', json=body)
        assert result.status_code == 201
        assert 'judge-secret' not in result.text
        assert app.state.store.task(result.json()['id'], private=True)['secrets']['judge_api_key'] == 'judge-secret'


def test_non_judge_task_does_not_call_judge(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, 'readiness', lambda: {'ready': True})
    monkeypatch.setattr(Environment, 'validated', lambda *args: True)
    calls = []
    monkeypatch.setattr(connectivity, 'check', lambda *args: calls.append(args) or {'ready': True})
    with TestClient(create_app(tmp_path, run_jobs=False), headers=HEADERS) as client:
        obj = client.post('/api/objects', json={'name': 'target', 'url': 'https://target.example/v1'}).json()
        result = client.post('/api/tasks', json={'object_id': obj['id'], 'model': 'target', 'mode': 'eval', 'datasets': ['arxivmath'], 'options': {'judge-model': 'unused'}, 'judge_api_key': 'unused'})
        assert result.status_code == 201 and len(calls) == 1
        assert calls[0][1] == 'target'


def test_judge_defaults_follow_framework_file(tmp_path, monkeypatch):
    source = tmp_path / 'thvv/eval/scripts/run_eval.py'
    source.parent.mkdir(parents=True)
    source.write_text("parser.add_argument('--judge_model', default=os.environ.get('JUDGE_MODEL', 'native-model'))\nparser.add_argument('--judge_base_url', default=os.environ.get('JUDGE_BASE_URL', 'https://native.example/v1'))")
    monkeypatch.setattr(engine, 'ROOT', tmp_path)
    assert engine.judge_config({}) == {'model': 'native-model', 'url': 'https://native.example/v1'}
