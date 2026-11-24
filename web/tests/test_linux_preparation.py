import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import threading

import pytest

from thvv import prepare_linux as system
from web import cleanup, engine
from web.environment import Environment


def test_inspect_java_checks_actual_version_and_never_installs(monkeypatch):
    monkeypatch.setattr(system.shutil, 'which', lambda name, **kwargs: '/fixture/java')
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, '', 'openjdk version "1.8.0_514"')
    monkeypatch.setattr(system.subprocess, 'run', run)
    assert system.inspect_component('java', {'PATH': '/fixture'})['ready']
    assert calls == [['/fixture/java', '-version']]
    monkeypatch.setattr(system.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, 0, '', 'openjdk version "17.0.21"'))
    assert not system.inspect_component('java')['ready']


@pytest.mark.parametrize('code,stdout,stderr,ready,reason', [
    (0, '29.0.0\n', '', True, 'ready'),
    (1, '', 'permission denied while connecting to socket', False, 'permission'),
    (1, '', 'Cannot connect to the Docker daemon', False, 'stopped'),
])
def test_docker_probe_distinguishes_access_and_service_state(monkeypatch, code, stdout, stderr, ready, reason):
    monkeypatch.setattr(system.shutil, 'which', lambda name, **kw: '/fixture/docker')
    monkeypatch.setattr(system.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, code, stdout, stderr))
    result = system.inspect_component('docker')
    assert result['ready'] is ready and result['code'] == reason


def test_reuse_does_not_install_start_or_change_permissions(tmp_path, monkeypatch):
    monkeypatch.setattr(system, 'inspect_component', lambda *a: {'ready': True, 'version': '1.8', 'automatic': True})
    monkeypatch.setattr(system.subprocess, 'run', lambda *a, **kw: pytest.fail('ready components must be reused'))
    monkeypatch.setattr(system, 'install_java', lambda *a: pytest.fail('ready Java must be reused'))
    for component in ['docker', 'java']:
        assert system.prepare_component(component, tmp_path, {})['disposition'] == 'reused'


def test_prepare_on_non_linux_does_not_install_anything(tmp_path, monkeypatch):
    monkeypatch.setattr(system.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(system, 'inspect_component', lambda *a: {'ready': False, 'automatic': False})
    monkeypatch.setattr(system.subprocess, 'run', lambda *a, **kw: pytest.fail('non-Linux must stay read-only'))
    monkeypatch.setattr(system, 'install_java', lambda *a: pytest.fail('non-Linux must not install Java'))
    for component in ['docker', 'java']:
        result = system.prepare_component(component, tmp_path, {})
        assert not result['ready'] and not result['automatic'] and 'Linux' in result['detail']


def missing_docker(monkeypatch, code='stopped'):
    monkeypatch.setattr(system.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(system, 'inspect_component', lambda *a: {'ready': False, 'automatic': True, 'code': code, 'detail': 'fixture unavailable'})


@pytest.mark.parametrize('env', [{'DOCKER_HOST': 'tcp://other-host:2376'}, {'DOCKER_CONTEXT': 'team-remote'}])
def test_remote_docker_is_never_managed_locally(tmp_path, monkeypatch, env):
    missing_docker(monkeypatch)
    monkeypatch.setattr(system.subprocess, 'run', lambda *a, **kw: pytest.fail('must not install or start local Docker'))
    result = system.prepare_component('docker', tmp_path, env)
    assert not result['ready'] and not result['automatic'] and '独立' in result['detail']


def test_docker_permission_denied_does_not_modify_user_groups(tmp_path, monkeypatch):
    missing_docker(monkeypatch, 'permission')
    monkeypatch.setattr(system.subprocess, 'run', lambda *a, **kw: pytest.fail('must not change access permissions'))
    assert not system.prepare_component('docker', tmp_path, {})['automatic']


def test_docker_without_systemd_explains_host_preparation(tmp_path, monkeypatch):
    missing_docker(monkeypatch)
    monkeypatch.setattr(Path, 'is_dir', lambda self: False)
    monkeypatch.setattr(system.subprocess, 'run', lambda *a, **kw: pytest.fail('cannot start service'))
    result = system.prepare_component('docker', tmp_path, {})
    assert '宿主机' in result['detail'] and not result['automatic']


def test_docker_without_admin_fails_promptly_without_password_prompt(tmp_path, monkeypatch):
    missing_docker(monkeypatch)
    monkeypatch.setattr(Path, 'is_dir', lambda self: True)
    monkeypatch.setattr(system.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(system.shutil, 'which', lambda *a: '/usr/bin/sudo')
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1)
    monkeypatch.setattr(system.subprocess, 'run', run)
    result = system.prepare_component('docker', tmp_path, {})
    assert not result['ready'] and '管理员' in result['detail']
    assert calls == [['/usr/bin/sudo', '-n', 'true']]


@pytest.mark.parametrize('accessible', [True, False])
def test_docker_install_is_followed_by_same_user_recheck(tmp_path, monkeypatch, accessible):
    monkeypatch.setattr(system.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(Path, 'is_dir', lambda self: True)
    monkeypatch.setattr(system.os, 'geteuid', lambda: 0)
    reports = iter([{'ready': False, 'automatic': True, 'code': 'missing'},
                    {'ready': accessible, 'automatic': True, 'code': 'ready' if accessible else 'permission', 'version': '29.0.0'}])
    monkeypatch.setattr(system, 'inspect_component', lambda *a: next(reports))
    calls = []
    monkeypatch.setattr(system.subprocess, 'run', lambda args, **kw: calls.append(args))
    result = system.prepare_component('docker', tmp_path, {})
    assert result['disposition'] == ('prepared' if accessible else None)
    assert result['ready'] is accessible and result['automatic'] is accessible
    assert len(calls) == 1 and calls[0][0] == 'bash' and calls[0][1].endswith('thvv/prepare-docker.sh')


def archive_fixture(monkeypatch, *, invalid_checksum=False, traversal=False):
    stream = io.BytesIO()
    payload = b'#!/bin/sh\nprintf \'openjdk version "1.8.0_514"\\n\' >&2\n'
    with tarfile.open(fileobj=stream, mode='w:gz') as tar:
        item = tarfile.TarInfo('../../escape' if traversal else 'jre8/bin/java')
        item.mode, item.size = 0o755, len(payload)
        tar.addfile(item, io.BytesIO(payload))
    content = stream.getvalue()
    package = {'download_url': 'https://cdn.azul.com/zulu/bin/fixture.tar.gz',
               'sha256_hash': '0' * 64 if invalid_checksum else hashlib.sha256(content).hexdigest(),
               'java_version': [8, 0, 514], 'os': 'linux', 'availability_type': 'CA',
               'lib_c_type': 'glibc', 'java_package_type': 'jre', 'hw_bitness': 64, 'arch': 'arm'}
    monkeypatch.setattr(system, 'java_platform', lambda: 'aarch64')
    monkeypatch.setattr(system, 'fetch_json', lambda url: [{'package_uuid': 'a' * 36}] if '?' in url else package)
    def download(*a, **kw):
        response = io.BytesIO(content)
        response.url = package['download_url']
        return response
    monkeypatch.setattr(system, 'urlopen', download)


def test_java_installs_verified_archive_and_is_used_by_engine(tmp_path, monkeypatch):
    archive_fixture(monkeypatch)
    system.install_java(tmp_path)
    env = engine.engine_env(tmp_path)
    assert env['JAVA_HOME'] == str(tmp_path / system.JAVA_DIRECTORY)
    assert system.inspect_component('java', env)['ready']
    assert (tmp_path / system.JAVA_DIRECTORY / '.thvv-managed.json').is_file()
    assert not list((tmp_path / 'runtimes').glob('.java-download-*'))


@pytest.mark.parametrize('bad', ['checksum', 'traversal'])
def test_unverified_or_unsafe_java_archive_is_not_installed(tmp_path, monkeypatch, bad):
    archive_fixture(monkeypatch, invalid_checksum=bad == 'checksum', traversal=bad == 'traversal')
    with pytest.raises((ValueError, tarfile.FilterError)):
        system.install_java(tmp_path)
    assert not (tmp_path / system.JAVA_DIRECTORY).exists()
    assert not (tmp_path / 'escape').exists()


def test_system_prepare_reports_failure_without_claiming_ready(tmp_path, monkeypatch):
    env = Environment(tmp_path, threading.RLock(), lambda: False)
    env.current = {'items': [], 'log': '', 'scope': 'environment'}
    missing = {'ready': False, 'automatic': True, 'detail': 'missing'}
    monkeypatch.setattr('web.environment.inspect_component', lambda *a: missing)
    calls = []
    def command(args, **kwargs):
        calls.append(args)
        Path(args[-1]).write_text(json.dumps({**missing, 'automatic': False, 'detail': '需要管理员授权'}))
        return False
    monkeypatch.setattr(env, 'command', command)
    env.system_component('docker', 'Docker 服务', False)
    assert calls == [] and env.current['items'][0]['state'] == 'missing'
    env.system_component('docker', 'Docker 服务', True)
    assert len(calls) == 1
    row = env.current['items'][0]
    assert row['state'] == 'manual' and row['disposition'] is None and '管理员' in row['detail']


def test_cached_docker_cannot_allow_test_after_daemon_stops(tmp_path, monkeypatch):
    env = Environment(tmp_path, threading.RLock(), lambda: False)
    monkeypatch.setattr(env, 'readiness', lambda spec: {'environment': True, 'data': True})
    monkeypatch.setattr('web.environment.inspect_component', lambda *a: {'ready': False})
    assert not env.validated({'mode': 'eval', 'datasets': ['live_code_bench']})
    assert env.validated({'mode': 'eval', 'datasets': ['arxivmath']})


def test_cleanup_only_includes_managed_project_java(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, 'ROOT', tmp_path)
    monkeypatch.delenv('THVV_ENGINE_PYTHON', raising=False)
    data = tmp_path / 'state'
    java = data / system.JAVA_DIRECTORY
    java.mkdir(parents=True)
    (java / 'keep-unmanaged').touch()
    assert cleanup.inventory('environment', {}, [], data_dir=data)['entries'] == []
    (java / '.thvv-managed.json').write_text('{}')
    entries = cleanup.inventory('environment', {}, [], data_dir=data)['entries']
    assert len(entries) == 1 and entries[0]['path'] == str(java)
    cleanup.execute(entries)
    assert not java.exists()
