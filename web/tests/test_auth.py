import json
import time

from fastapi.testclient import TestClient
import pytest

from web.app import create_app
from web import auth
from web.tests.auth_helpers import HEADERS, PASSWORD


@pytest.fixture
def client(tmp_path):
    auth.configure(tmp_path, PASSWORD)
    with TestClient(create_app(tmp_path, run_jobs=False), headers=HEADERS, follow_redirects=False) as client:
        yield client


def login(client, **overrides):
    return client.post('/api/auth/login', json={'username': 'admin', 'password': PASSWORD, **overrides})


def test_login_is_required_for_ui_apis_logs_and_reports(client):
    for path in ['/', '/index.html', '/app.js', '/docs', '/openapi.json', '/login.html']:
        response = client.get(path)
        assert response.status_code == 303 and response.headers['location'] == '/login'
        assert response.headers['cache-control'] == 'no-store'
    for path in ['/api/meta', '/api/objects', '/api/tasks', '/api/environment', '/api/tasks/source',
                 '/api/tasks/source/logs', '/api/tasks/source/artifacts/report.html', '/api/tasks/source/artifacts/report.zip']:
        assert client.get(path).status_code == 401
    for method, path in [('POST', '/api/tasks'), ('POST', '/api/tasks/source/replay'), ('DELETE', '/api/tasks/source'),
                         ('POST', '/api/objects'), ('PATCH', '/api/objects/source'), ('DELETE', '/api/objects/source'),
                         ('POST', '/api/connectivity'), ('POST', '/api/judge-connectivity'),
                         ('POST', '/api/environment/check'), ('POST', '/api/environment/prepare'),
                         ('POST', '/api/environment/readiness'), ('POST', '/api/environment/cleanup-preview'), ('POST', '/api/environment/cleanup'),
                         ('POST', '/api/auth/logout')]:
        assert client.request(method, path, json={}).status_code == 401
    assert client.app.state.store.rows('SELECT * FROM objects') == []
    assert client.app.state.store.rows('SELECT * FROM tasks') == []


def test_login_assets_are_public_but_never_contain_the_password(client, tmp_path):
    for path in ['/login', '/login.js', '/login.css']:
        response = client.get(path)
        assert response.status_code == 200
        assert PASSWORD not in response.text and response.headers['cache-control'] == 'no-store'
    assert "form-action 'self'" in client.get('/login').headers['content-security-policy']
    config = tmp_path / 'auth.json'
    assert PASSWORD not in config.read_text()
    assert config.stat().st_mode & 0o777 == 0o600


def test_login_cookie_unlocks_console_and_logout_revokes_it(client):
    response = login(client)
    assert response.status_code == 200
    cookie = response.headers['set-cookie']
    assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie and 'Path=/' in cookie
    assert f'Max-Age={auth.SESSION_SECONDS}' in cookie and 'Secure' not in cookie
    token = client.cookies.get(auth.COOKIE)
    stored = client.app.state.store.rows('SELECT * FROM sessions')
    assert len(stored) == 1 and token not in json.dumps(stored) and PASSWORD not in json.dumps(stored)
    assert client.get('/').status_code == 200
    assert client.get('/api/objects').status_code == 200
    assert client.get('/login').headers['location'] == '/'
    response = client.post('/api/auth/logout')
    assert response.status_code == 200 and 'Max-Age=0' in response.headers['set-cookie']
    assert client.get('/api/tasks', headers={'Cookie': f'{auth.COOKIE}={token}'}).status_code == 401
    assert client.get('/').status_code == 303


def test_bad_credentials_and_validation_do_not_return_passwords(client):
    for credentials in [{'password': 'wrong-private-value'}, {'username': 'not-admin'}, {'username': '管理员'}]:
        response = login(client, **credentials)
        assert response.status_code == 401 and response.json()['detail'] == '账号或密码错误'
        assert 'set-cookie' not in response.headers
        assert 'wrong-private-value' not in response.text and PASSWORD not in response.text
    response = login(client, password=['malformed-secret-value'])
    assert response.status_code == 422 and 'malformed-secret-value' not in response.text
    assert client.get('/api/meta').status_code == 401


def test_csrf_guards_cover_login_and_logout(client):
    body = {'username': 'admin', 'password': PASSWORD}
    for headers in [{'X-THVV-Request': ''}, {'Origin': 'https://unrelated.example'}, {'Origin': 'null'}]:
        assert client.post('/api/auth/login', json=body, headers=headers).status_code == 403
    assert client.post('/api/auth/login', json=body, headers={'Origin': 'http://testserver'}).status_code == 200
    assert client.post('/api/auth/logout', headers={'Origin': 'https://unrelated.example'}).status_code == 403
    assert client.get('/api/objects').status_code == 200


def test_rotation_expiration_and_forged_cookies(client):
    assert login(client).status_code == 200
    previous = client.cookies.get(auth.COOKIE)
    assert login(client).status_code == 200
    current = client.cookies.get(auth.COOKIE)
    assert current != previous
    for token in [previous, 'x' * 43, 'invalid', 'non-ascii-值']:
        if token.isascii():
            assert client.get('/api/tasks', headers={'Cookie': f'{auth.COOKIE}={token}'}).status_code == 401
        else:
            assert not client.app.state.auth.authenticated(token)
    assert client.get('/api/tasks').status_code == 200
    client.app.state.store.execute('UPDATE sessions SET expires=?', (time.time() - 1,))
    assert client.get('/api/tasks').status_code == 401


def test_https_cookie_is_secure(tmp_path):
    auth.configure(tmp_path, PASSWORD)
    with TestClient(create_app(tmp_path, run_jobs=False), headers=HEADERS, base_url='https://testserver') as client:
        assert 'Secure' in login(client).headers['set-cookie']
        assert client.get('/api/tasks').status_code == 200


def test_repeated_failures_are_rate_limited_then_recover(client, monkeypatch):
    monkeypatch.setattr(auth, 'MAX_ATTEMPTS', 3)
    for _ in range(3):
        assert login(client, password='wrong').status_code == 401
    response = login(client)
    assert response.status_code == 429 and int(response.headers['retry-after']) > 0
    for i in range(len(client.app.state.auth.failures)):
        client.app.state.auth.failures[i] -= auth.ATTEMPT_WINDOW + 1
    assert login(client).status_code == 200


def test_missing_or_invalid_auth_config_fails_closed(tmp_path):
    with pytest.raises(RuntimeError, match='登录配置缺失或无效'):
        create_app(tmp_path, run_jobs=False)
    (tmp_path / 'auth.json').write_text('{invalid}')
    with pytest.raises(RuntimeError, match='登录配置缺失或无效'):
        create_app(tmp_path, run_jobs=False)


def test_sessions_survive_restart_and_password_reset_preserves_objects_and_history(tmp_path):
    auth.configure(tmp_path, PASSWORD)
    with TestClient(create_app(tmp_path, run_jobs=False), headers=HEADERS) as client:
        assert login(client).status_code == 200
        token = client.cookies.get(auth.COOKIE)
        obj = client.post('/api/objects', json={'name': '保留对象', 'url': 'http://localhost:9900/v1', 'api_key': 'keep-key'}).json()
        store = client.app.state.store
        store.execute('INSERT INTO tasks(id,payload,secrets,status,created) VALUES(?,?,?,?,?)',
                      ('history', '{}', store.encrypt({}), 'succeeded', '2026-11-24'))
    with TestClient(create_app(tmp_path, run_jobs=False), headers={**HEADERS, 'Cookie': f'{auth.COOKIE}={token}'}) as client:
        assert client.get('/api/objects').json()[0]['id'] == obj['id']
    auth.configure(tmp_path, 'changed-test-password')
    with TestClient(create_app(tmp_path, run_jobs=False), headers=HEADERS) as client:
        assert client.get('/api/tasks', headers={'Cookie': f'{auth.COOKIE}={token}'}).status_code == 401
        assert login(client).status_code == 401
        assert login(client, password='changed-test-password').status_code == 200
        assert client.get('/api/objects').json()[0]['has_api_key']
        assert client.get('/api/tasks').json()[0]['id'] == 'history'
