import builtins
import json
from pathlib import Path
import subprocess
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

from web import dependency_probe, engine
from web.diagnostics import failure_details
from web.environment import Environment
from web.runtime_probe import load_native


REQUIREMENT = 'git+https://github.com/sierra-research/tau2-bench@v0.2.0'
ARCHIVE = 'https://github.com/sierra-research/tau2-bench/archive/refs/tags/v0.2.0.zip'


@pytest.mark.parametrize('installed', [False, True])
def test_native_precheck_installs_tau2_source_in_current_python_only_when_missing(tmp_path, monkeypatch, installed):
    monkeypatch.setattr(sys, 'path', list(sys.path))
    native = load_native()
    monkeypatch.setattr(sys, 'argv', ['run_eval.py', '--datasets', 'tau2_bench', '--model_name', 'fixture',
                                    '--base_url', 'http://localhost:9000/v1', '--api_key', 'fixture'])
    args = native.parse_args()
    original_import = builtins.__import__
    state = {'installed': installed}
    def importing(name, *args, **kwargs):
        if name == 'tau2':
            if not state['installed']:
                raise ImportError('tau2 missing')
            return ModuleType('tau2')
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', importing)
    monkeypatch.setattr(native.shutil, 'which', lambda name: '/fixture/' + name)
    calls = []
    def install(command, **kwargs):
        calls.append(command)
        state['installed'] = True
    monkeypatch.setattr(native.subprocess, 'run', install)
    monkeypatch.chdir(tmp_path)
    assert native.pre_check(args) == []
    if installed:
        assert calls == []
    else:
        assert len(calls) == 1 and calls[0][:3] == [sys.executable, '-m', 'pip']
        assert ARCHIVE in calls[0]
        requirements = Path(calls[0][calls[0].index('-r') + 1])
        assert requirements.is_absolute() and requirements.is_file()


def test_tau2_dependency_check_matches_source_tag_instead_of_package_version(monkeypatch):
    monkeypatch.setattr(dependency_probe.metadata, 'packages_distributions', lambda: {'tau2': ['tau2']})
    source = {'url': 'https://github.com/sierra-research/tau2-bench',
              'vcs_info': {'requested_revision': 'v0.2.0', 'commit_id': 'fixture'}}
    monkeypatch.setattr(dependency_probe.metadata, 'distribution', lambda _: SimpleNamespace(
        version='0.2.1-dev', read_text=lambda name: json.dumps(source)))
    monkeypatch.setattr(dependency_probe, 'import_module', lambda _: None)
    report = dependency_probe.check_optional(REQUIREMENT, 'tau2')
    assert report['ready'] and report['versions'][0]['source_revision'] == 'v0.2.0'
    source['vcs_info']['requested_revision'] = 'v1.0.0'
    assert not dependency_probe.check_optional(REQUIREMENT, 'tau2')['ready']
    source.clear()
    source.update(url=ARCHIVE, archive_info={'hashes': {'sha256': 'a' * 64}})
    assert dependency_probe.check_optional(REQUIREMENT, 'tau2')['ready']
    source['url'] = ARCHIVE.replace('v0.2.0', 'v1.0.0')
    assert not dependency_probe.check_optional(REQUIREMENT, 'tau2')['ready']


def test_data_preparation_blocks_missing_tau2_without_install_or_download(tmp_path, monkeypatch):
    env = Environment(tmp_path, threading.RLock(), lambda: False)
    env.current = {'items': [], 'log': '', 'scope': 'data'}
    monkeypatch.setattr(engine, 'readiness', lambda: {'ready': True})
    monkeypatch.setattr(env, 'command', lambda *a, **kw: pytest.fail('data action must not install dependencies'))
    calls = []
    def probe(kind, name, options, prepare=False):
        calls.append(prepare)
        return failure_details('ImportError: `tau2` not found. Please run `pip install ' + REQUIREMENT + '` to use τ²-bench.')
    monkeypatch.setattr(env, 'probe', probe)
    env.check_data({'mode': 'eval', 'datasets': ['tau2_bench'], 'options': {}}, prepare=True)
    assert calls == [False]
    item = env.current['items'][0]
    assert item['state'] == 'blocked' and item['error_code'] == 'missing_dependency'
    assert '运行环境' in item['detail'] and 'v0.2.0' in item['detail']
    assert item['disposition'] is None


def test_optional_preparation_uses_engine_python_and_preserves_core_constraints(tmp_path, monkeypatch):
    env = Environment(tmp_path, threading.RLock(), lambda: False)
    env.current = {'items': [], 'log': '', 'scope': 'environment'}
    reports = iter([{'ready': False}, {'ready': True}])
    monkeypatch.setattr(env, 'dependency_report', lambda *a: next(reports))
    calls = []
    monkeypatch.setattr(env, 'command', lambda args, **kw: calls.append(args) or True)
    env.package('tau2-bench', 'tau2', REQUIREMENT, True)
    assert calls == [[str(engine.engine_python()), '-m', 'pip', 'install', '-r', 'web/engine-requirements.txt', ARCHIVE]]
    assert env.current['items'][0]['state'] == 'ready'


@pytest.mark.parametrize('cached', [True, False])
def test_native_tau2_reuses_only_local_snapshot_and_prepare_can_repair_cache(tmp_path, monkeypatch, cached):
    monkeypatch.setattr(sys, 'path', list(sys.path))
    native = load_native()
    monkeypatch.setattr(sys, 'argv', ['run_eval.py', '--datasets', 'tau2_bench', '--model_name', 'fixture',
                                    '--base_url', 'http://localhost:9000/v1', '--api_key', 'fixture',
                                    '--dataset_dir', str(tmp_path / 'project-data')])
    args = native.parse_args()
    snapshot = tmp_path / 'snapshot'
    snapshot.mkdir()
    calls = []
    def cached_snapshot(**kwargs):
        calls.append(kwargs)
        assert kwargs == {'repo_id': 'evalscope/tau2-bench-data', 'repo_type': 'dataset', 'local_files_only': True}
        if not cached:
            raise FileNotFoundError('no cached snapshot')
        return str(snapshot)
    for name in ['modelscope', 'modelscope.hub', 'modelscope.hub.snapshot_download']:
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    sys.modules['modelscope.hub.snapshot_download'].snapshot_download = cached_snapshot
    command, _ = native.build_eval_command(args, 'tau2_bench')
    config = json.loads(command[command.index('--dataset-args') + 1])['tau2_bench']
    assert config.get('local_path') == (str(snapshot) if cached else None)
    calls.clear()
    command, _ = native.build_eval_command(args, 'tau2_bench', use_cached_tau2=False)
    assert calls == []
    assert 'local_path' not in json.loads(command[command.index('--dataset-args') + 1])['tau2_bench']


def test_offline_probe_uses_bundled_litellm_metadata():
    script = "from web.runtime_probe import offline; import os; offline(); assert os.environ['LITELLM_LOCAL_MODEL_COST_MAP']=='True'"
    subprocess.run([sys.executable, '-c', script], check=True, cwd=engine.ROOT)
