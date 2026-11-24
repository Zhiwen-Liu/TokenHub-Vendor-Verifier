"""Preview and remove only bounded, named runtime resources."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess

from . import engine


def signature(path):
    """Include children so a changed preview cannot authorize new content."""
    entries = []
    for current, dirs, files in os.walk(path, followlinks=False):
        for name in sorted(dirs + files):
            item = Path(current) / name
            stat = item.lstat()
            entries.append((str(item.relative_to(path)), stat.st_size, stat.st_mtime_ns, stat.st_ino))
    stat = path.lstat()
    return hashlib.sha256(repr((stat.st_ino, stat.st_mtime_ns, sorted(entries))).encode()).hexdigest()


def size(path):
    if not path.is_dir():
        return path.lstat().st_size
    return sum((Path(base) / f).lstat().st_size for base, _, files in os.walk(path, followlinks=False)
               for f in files)


def inventory(scope, spec, tokenizer_names):
    entries, notes = [], []

    def add(path, root, label, replacement=None):
        path, root = Path(path), Path(root)
        if not path.exists() or path.is_symlink() or root.is_symlink():
            return
        if not path.resolve().is_relative_to(root.resolve()) or path.resolve() == root.resolve():
            raise ValueError('清理路径超出允许范围')
        entries.append({'path': str(path), 'root': str(root), 'label': label,
                        'bytes': size(path), 'signature': signature(path), 'replacement': replacement})

    if scope == 'environment':
        target = engine.ROOT / '.venv-engine'
        if engine.engine_python() != (target / 'bin/python').absolute():
            raise ValueError('当前使用外部评测环境，网页不会删除外部环境；请在该环境中手动管理依赖。')
        add(target, engine.ROOT, '本项目评测依赖环境（.venv-engine）')
        notes.append('仅移除评测依赖；网页服务、测试对象、API 密钥、历史任务和数据缓存均保留。Docker、Java 等系统组件不会卸载。')
    else:
        add(engine.ROOT / 'thvv/eval/datasets/datasets', engine.ROOT / 'thvv/eval/datasets', '本项目效果评测生成的数据缓存')
        if spec['mode'] == 'perf':
            from .environment import selected_buckets
            for bucket in selected_buckets(spec):
                relative = f'thvv/perf/datasets_clean/perf_zh_{bucket}.jsonl'
                path = engine.ROOT / relative
                pointer = subprocess.check_output(['git', 'show', 'HEAD:' + relative], cwd=engine.ROOT)
                match = re.fullmatch(rb'version https://git-lfs.github.com/spec/v1\noid sha256:([0-9a-f]{64})\nsize (\d+)\n', pointer)
                if not match or not path.is_file() or path.is_symlink() or path.stat().st_size < 200:
                    continue
                with path.open('rb') as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                if digest != match[1].decode():
                    notes.append(f'{bucket} 档文件与仓库版本不同，保留此文件。')
                    continue
                add(path, engine.ROOT / 'thvv/perf/datasets_clean', f'压测数据 · {bucket}（恢复下载占位文件）', pointer.decode())
        ms_root = Path(os.environ.get('MODELSCOPE_CACHE', Path.home() / '.cache/modelscope')) / 'models'
        hf_home = Path(os.environ.get('HF_HOME', Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'huggingface'))
        hf_root = Path(os.environ.get('HF_HUB_CACHE', os.environ.get('HUGGINGFACE_HUB_CACHE', hf_home / 'hub')))
        for name in tokenizer_names:
            if not re.fullmatch(r'[\w.-]+/[\w.-]+', name) or any(part in ('.', '..') for part in name.split('/')):
                notes.append(f'自定义本地 Tokenizer 保留：{name}')
                continue
            for root, folder, source in [(ms_root, name.replace('/', '--'), '魔搭'),
                                         (hf_root, 'models--' + name.replace('/', '--'), 'HF')]:
                path = root / folder
                # A shared repo may also contain model weights. Never delete those.
                if path.is_dir() and any(p.suffix in ('.safetensors', '.bin', '.pt', '.pth', '.onnx', '.gguf')
                                          for p in (path / 'snapshots').rglob('*')):
                    notes.append(f'{source} {name} 缓存含模型权重，保留整个目录。')
                    continue
                add(path, root, f'Tokenizer · {name}（{source}共享缓存）')
        notes.append('保留测试对象、密钥、历史任务、依赖环境、Git LFS 原始对象、手动放置的数据，以及 Hugging Face / 魔搭的数据集原始共享缓存。列出的 Tokenizer 缓存可能被其他项目复用。')
    return {'entries': entries, 'notes': notes, 'bytes': sum(e['bytes'] for e in entries)}


def execute(entries):
    # Recheck every path before removing any item; no paths come from client input.
    for item in entries:
        path, root = Path(item['path']), Path(item['root'])
        if path.is_symlink() or root.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or path.resolve() == root.resolve():
            raise ValueError('清理路径已变化，请重新预览')
        if not path.exists() or signature(path) != item['signature']:
            raise ValueError('待清理内容已变化，请重新预览')
    for item in entries:
        path = Path(item['path'])
        if item['replacement'] is not None:
            path.write_text(item['replacement'])
        else:
            shutil.rmtree(path)
