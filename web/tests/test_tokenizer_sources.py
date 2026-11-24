from pathlib import Path

import pytest

from thvv.perf.scripts import setup_tokenizer as tokenizer
from web import engine
from web.diagnostics import failure_details


def test_engine_sets_mirror_before_imports_and_preserves_explicit_endpoint(monkeypatch):
    monkeypatch.delenv('HF_ENDPOINT', raising=False)
    monkeypatch.delenv('HF_HUB_DISABLE_IMPLICIT_TOKEN', raising=False)
    monkeypatch.delenv('HF_HUB_DISABLE_XET', raising=False)
    env = engine.engine_env()
    assert env['HF_ENDPOINT'] == 'https://hf-mirror.com'
    assert env['HF_HUB_DISABLE_IMPLICIT_TOKEN'] == '1'
    assert env['HF_HUB_DISABLE_XET'] == '1'
    monkeypatch.setenv('HF_ENDPOINT', 'https://custom.example')
    assert engine.engine_env()['HF_ENDPOINT'] == 'https://custom.example'


@pytest.mark.parametrize('cached_source', ['modelscope', 'huggingface'])
def test_offline_probe_recognizes_either_cache(tmp_path, monkeypatch, cached_source):
    calls, verified = [], []
    def snapshot(source, name, local_only):
        calls.append((source, local_only))
        assert local_only, 'readiness checks must never download'
        if source != cached_source:
            raise FileNotFoundError('no cache')
        return str(tmp_path)
    monkeypatch.setattr(tokenizer, '_snapshot', snapshot)
    monkeypatch.setattr(tokenizer, '_verify', verified.append)
    assert tokenizer.resolve_tokenizer('org/model') == str(tmp_path)
    assert verified == [str(tmp_path)]
    assert calls[-1] == (cached_source, True)


@pytest.mark.parametrize('invalid_modelscope_files', [False, True])
def test_prepare_falls_back_to_mirror_and_returns_verified_path(tmp_path, monkeypatch, invalid_modelscope_files):
    calls, verified = [], []
    def snapshot(source, name, local_only):
        calls.append((source, local_only))
        if local_only or (source == 'modelscope' and not invalid_modelscope_files):
            raise FileNotFoundError('repository or cache missing')
        return str(tmp_path / source)
    def verify(path):
        verified.append(path)
        if Path(path).name == 'modelscope':
            raise ValueError('incomplete tokenizer')
    monkeypatch.setattr(tokenizer, '_snapshot', snapshot)
    monkeypatch.setattr(tokenizer, '_verify', verify)
    assert tokenizer.resolve_tokenizer('org/model', download=True) == str(tmp_path / 'huggingface')
    assert calls == [('modelscope', True), ('huggingface', True), ('modelscope', False), ('huggingface', False)]
    assert verified[-1] == str(tmp_path / 'huggingface')


def test_modelscope_success_does_not_download_from_hf(tmp_path, monkeypatch):
    calls = []
    def snapshot(source, name, local_only):
        calls.append((source, local_only))
        if local_only:
            raise FileNotFoundError('no cache')
        return str(tmp_path)
    monkeypatch.setattr(tokenizer, '_snapshot', snapshot)
    monkeypatch.setattr(tokenizer, '_verify', lambda path: None)
    assert tokenizer.resolve_tokenizer('org/model', download=True) == str(tmp_path)
    assert calls[-1] == ('modelscope', False)
    assert ('huggingface', False) not in calls


def test_local_directory_is_verified_without_hub_lookup(tmp_path, monkeypatch):
    verified = []
    monkeypatch.setattr(tokenizer, '_verify', verified.append)
    monkeypatch.setattr(tokenizer, '_snapshot', lambda *args: pytest.fail('must use explicit local directory'))
    assert tokenizer.resolve_tokenizer(str(tmp_path), download=True) == str(tmp_path)
    with pytest.raises(FileNotFoundError):
        tokenizer.resolve_tokenizer(str(tmp_path / 'missing'), download=True)
    assert verified == [str(tmp_path)]


def test_missing_cache_does_not_trigger_download_and_original_hf_error_is_readable(monkeypatch):
    calls = []
    def snapshot(source, name, local_only):
        calls.append(local_only)
        raise FileNotFoundError('no cache')
    monkeypatch.setattr(tokenizer, '_snapshot', snapshot)
    with pytest.raises(FileNotFoundError, match='本地缓存不完整'):
        tokenizer.resolve_tokenizer('org/model')
    assert calls == [True, True]
    raw = "We couldn't connect to 'https://huggingface.co' to load the files, and couldn't find them in the cached files."
    result = failure_details(raw, kind='tokenizer')
    assert result['detail'] == 'Tokenizer本地缓存不完整，在数据分组中点击“准备缺失项”补齐。'
    assert result['error_details'] == raw
