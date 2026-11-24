import json
import threading
import time

from web.tests.auth_helpers import AuthenticatedClient as TestClient, create_app
import httpx
import pytest

from web import connectivity, engine
from web.environment import Environment, fingerprint, tokenizers, dependency_stamp
from web.diagnostics import failure_details
from web import dependency_probe
from web.runtime_probe import offline, OfflineCacheMiss

HEADERS = {'X-THVV-Request': '1'}
SPEC = {'mode': 'eval', 'model': 'test-model', 'datasets': ['arxivmath'], 'options': {}}


def mock_http(monkeypatch, handler):
    client = httpx.Client
    monkeypatch.setattr(connectivity.httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))


@pytest.mark.parametrize('protocol,suffix,header,response', [
    ('openai', '/chat/completions', 'authorization', {'choices': [{'message': {'role': 'assistant', 'content': 'Hi'}}]}),
    ('anthropic', '/messages', 'x-api-key', {'type': 'message', 'content': [{'type': 'text', 'text': 'Hi'}]}),
])
def test_connectivity_uses_selected_model_and_protocol(monkeypatch, protocol, suffix, header, response):
    def handler(request):
        assert str(request.url) == 'https://vendor.example/v1' + suffix
        assert 'test-secret' in request.headers[header]
        assert json.loads(request.content) == {'model': 'requested-model', 'messages': [{'role': 'user', 'content': 'Hi'}], 'max_tokens': 1, 'stream': False}
        if protocol == 'anthropic':
            assert request.headers['anthropic-version'] == '2023-06-01'
        return httpx.Response(200, json=response)
    mock_http(monkeypatch, handler)
    result = connectivity.check('https://vendor.example/v1', 'requested-model', protocol, 'test-secret')
    assert result['ready'] and result['http_status'] == 200
    assert 'test-secret' not in json.dumps(result)


def test_http_200_html_is_not_ready_and_key_is_optional(monkeypatch):
    def handler(request):
        assert 'authorization' not in request.headers
        return httpx.Response(200, text='<h1>gateway login</h1>')
    mock_http(monkeypatch, handler)
    assert not connectivity.check('https://vendor.example/v1', 'm', 'openai')['ready']


def test_connectivity_error_redacts_key_and_never_follows_redirect(monkeypatch):
    seen = []
    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={'Location': 'https://elsewhere.example'}, json={'error': {'message': 'bad key private-key'}})
    mock_http(monkeypatch, handler)
    result = connectivity.check('https://vendor.example/v1', 'm', 'openai', 'private-key')
    assert len(seen) == 1 and not result['ready']
    assert 'private-key' not in result['message']


def test_start_requires_fresh_environment_and_successful_live_request(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, 'readiness', lambda: {'ready': True})
    calls = []
    def probe(*args):
        calls.append(args)
        return {'ready': False, 'message': 'HTTP 401 · 认证失败'}
    monkeypatch.setattr(connectivity, 'check', probe)
    app = create_app(tmp_path, run_jobs=False)
    with TestClient(app, headers=HEADERS) as client:
        obj = client.post('/api/objects', json={'name': 'test', 'url': 'http://localhost:9000/v1', 'api_key': 'saved-key'}).json()
        body = {**SPEC, 'object_id': obj['id'], 'protocol': 'openai'}
        assert client.post('/api/tasks', json=body).status_code == 409
        assert calls == []
        env = app.state.environment
        env.groups['environment'] = {'status': 'completed', 'ready': True, 'requirements': ['core'], 'stamp': dependency_stamp()}
        env.groups['data'] = {'status': 'completed', 'ready': True, 'fingerprint': fingerprint(SPEC), 'finished': time.time(), 'stamp': dependency_stamp()}
        r = client.post('/api/tasks', json=body)
        assert r.status_code == 409 and '401' in r.text
        assert calls[-1][-1] == 'saved-key'
        assert client.get('/api/tasks').json() == []
        env.groups['data']['finished'] = time.time() - 301
        assert client.post('/api/tasks', json=body).status_code == 409
        assert len(calls) == 1


def wait_job(env):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        state = env.snapshot()
        if state['status'] != 'running':
            return state
        time.sleep(.01)
    raise AssertionError('worker did not finish')


def test_preparation_downloads_only_missing_selected_resources_and_rechecks(tmp_path, monkeypatch):
    env = Environment(tmp_path, threading.RLock(), lambda: False)
    monkeypatch.setattr(env, 'core_ready', lambda: True)
    monkeypatch.setattr(env, 'command', lambda *args, **kwargs: True)
    calls = []
    def probe(kind, name, options, prepare=False):
        calls.append((kind, name, prepare))
        return {'ready': prepare, 'detail': '已下载' if prepare else '缺失'}
    monkeypatch.setattr(env, 'probe', probe)
    env.start(SPEC, prepare=True)
    assert wait_job(env)['ready']
    monkeypatch.setattr(engine, 'readiness', lambda: {'ready': True})
    monkeypatch.setattr(env, 'core_ready', lambda: pytest.fail('data preparation must not recheck dependencies'))
    env.start(SPEC, prepare=True, scope='data')
    result = wait_job(env)
    assert result['ready'] and env.validated(SPEC)
    assert calls == [('data', 'arxivmath', False), ('data', 'arxivmath', True)]
    assert not env.validated({**SPEC, 'datasets': ['mmlu_pro']})
    assert 'object_id' not in json.dumps(result)
    env.close()


def test_environment_does_not_claim_ready_when_manual_component_missing(tmp_path, monkeypatch):
    env = Environment(tmp_path, threading.RLock(), lambda: False)
    monkeypatch.setattr(env, 'core_ready', lambda: True)
    monkeypatch.setattr('web.environment.shutil.which', lambda name: None)
    monkeypatch.setattr(env, 'probe', lambda *args: {'ready': True, 'detail': '数据已缓存'})
    env.start({**SPEC, 'datasets': ['live_code_bench']})
    result = wait_job(env)
    assert not result['ready']
    assert next(i for i in result['items'] if i['id'] == 'docker')['state'] == 'manual'
    env.close()


def test_preparing_is_blocked_during_tests_and_restart_does_not_keep_ready(tmp_path):
    env = Environment(tmp_path, threading.RLock(), lambda: True)
    with pytest.raises(ValueError, match='当前测试任务结束'):
        env.start(SPEC, prepare=True)
    env.current = {'status': 'running', 'ready': True, 'items': [], 'id': 'old', 'scope': 'data'}
    env.persist()
    restarted = Environment(tmp_path, threading.RLock(), lambda: False)
    assert restarted.snapshot()['status'] == 'interrupted'
    assert not restarted.snapshot()['ready']


def test_tokenizer_defaults_follow_native_bucket_rules():
    base = {'mode': 'perf', 'model': 'gpt-4o', 'options': {'bucket': '1k'}}
    assert tokenizers(base) == ['Xenova/gpt-4o']
    assert tokenizers({**base, 'options': {'bucket': '512k'}}) == ['zai-org/GLM-4.6']
    assert tokenizers({**base, 'options': {'perf-command': 'bench-all', 'buckets': '1k 512k'}}) == ['Xenova/gpt-4o', 'zai-org/GLM-4.6']
    assert tokenizers({**base, 'options': {'bucket': '512k', 'tokenizer': '/local/tokenizer'}}) == ['/local/tokenizer']


def test_perf_data_distinguishes_lfs_pointers_empty_and_loaded_data(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, 'ROOT', tmp_path)
    folder = tmp_path / 'thvv/perf/datasets_clean'
    folder.mkdir(parents=True)
    path = folder / 'perf_zh_1k.jsonl'
    spec = {'mode': 'perf', 'options': {'bucket': '1k'}}
    for content in ('', 'version https://git-lfs.github.com/spec/v1\noid sha256:fixture\n', '<html>error</html>'):
        path.write_text(content)
        assert engine.missing_perf_data(spec) == ['1k']
    path.write_text('[{"role":"user","content":"test"}]\n')
    assert engine.missing_perf_data(spec) == []


def test_replay_connectivity_uses_saved_snapshot(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(connectivity, 'check', lambda *args: calls.append(args) or {'ready': True})
    app = create_app(tmp_path, run_jobs=False)
    with TestClient(app, headers=HEADERS) as client:
        store = app.state.store
        obj = {'id': 'deleted-object', 'name': 'original', 'url': 'http://original.example/v1'}
        payload = {'object': obj, 'spec': SPEC}
        store.execute('INSERT INTO tasks(id,payload,secrets,status,created) VALUES(?,?,?,?,?)', ('source', json.dumps(payload), store.encrypt({'api_key': 'original-secret'}), 'succeeded', '2026-11-23'))
        result = client.post('/api/connectivity', json={'object_id': obj['id'], 'replay_id': 'source', 'model': 'new-model', 'protocol': 'anthropic'})
        assert result.status_code == 200
        assert calls == [('http://original.example/v1', 'new-model', 'anthropic', 'original-secret')]


def test_offline_miss_escapes_sdk_retry_loops_without_waiting(monkeypatch):
    import socket
    # The disposable worker patches socket calls. Keep this test's process unchanged.
    with monkeypatch.context() as patch:
        for name in ('connect', 'connect_ex'):
            patch.setattr(socket.socket, name, getattr(socket.socket, name))
        for name in ('create_connection', 'getaddrinfo'):
            patch.setattr(socket, name, getattr(socket, name))
        for key in ('HF_HUB_OFFLINE', 'HF_DATASETS_OFFLINE', 'MODELSCOPE_OFFLINE'):
            patch.setenv(key, '0')
        offline()
        attempts = 0
        with pytest.raises(OfflineCacheMiss):
            for _ in range(5):
                attempts += 1
                try:
                    socket.getaddrinfo('uncached.example', 443)
                except Exception:
                    continue
        assert attempts == 1


def test_user_message_is_short_and_keeps_original_error_in_details():
    raw = 'NetworkError: HTTPSConnectionPool(host=source.example): Max retries exceeded ' + 'stack ' * 100
    result = failure_details(raw, code='missing_cache')
    assert len(result['detail']) < 60
    assert 'HTTPSConnectionPool' not in result['detail']
    assert result['error_details'] == raw
    assert '授权' in failure_details('HTTP 403 gated dataset', preparing=True)['detail']


def test_optional_version_mismatch_is_not_ready_even_if_module_importable(monkeypatch):
    monkeypatch.setattr(dependency_probe.metadata, 'version', lambda name: '3.0.0')
    monkeypatch.setattr(dependency_probe, 'import_module', lambda name: pytest.fail('must reject version before import'))
    result = dependency_probe.check_optional('swebench==4.1.0', 'swebench')
    assert not result['ready']
    assert result['versions'][0] == {'name': 'swebench', 'installed': '3.0.0', 'required': '==4.1.0', 'install_constraint': '==4.1.0', 'matches': False}


def test_version_check_uses_native_requirements_not_only_web_copy(tmp_path, monkeypatch):
    for name, content in {'thvv/eval/requirements.txt': 'evalscope==1.10.0\n', 'thvv/perf/requirements.txt': 'evalscope>=0.13.0\n', 'web/engine-requirements.txt': 'evalscope==1.9.0\n'}.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    monkeypatch.setattr(dependency_probe, 'ROOT', tmp_path)
    monkeypatch.setattr(dependency_probe.metadata, 'version', lambda name: '1.9.0')
    result = dependency_probe.check_core()
    assert not result['ready']
    assert '==1.10.0' in result['versions'][0]['required']
