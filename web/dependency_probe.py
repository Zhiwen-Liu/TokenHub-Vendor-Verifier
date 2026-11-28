"""Read installed versions against native constraints before importing components."""
from importlib import import_module, metadata
import json
from pathlib import Path
import re
import sys
from urllib.parse import quote

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]


def requirements(path):
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            yield Requirement(line)


def package_version(req, native=()):
    try:
        installed = metadata.version(req.name)
    except metadata.PackageNotFoundError:
        installed = None
    native_specs = list(dict.fromkeys(str(r.specifier) or '未限定版本' for r in native))
    aligned = bool(installed and installed in req.specifier and all(installed in r.specifier for r in native))
    return {'name': req.name, 'installed': installed, 'required': '；'.join(native_specs) or str(req.specifier) or '未限定版本',
            'install_constraint': str(req.specifier), 'matches': aligned}


def check_core(evalscope_min=None):
    native = {}
    for path in ('thvv/eval/requirements.txt', 'thvv/perf/requirements.txt'):
        for req in requirements(ROOT / path):
            native.setdefault(req.name.lower(), []).append(req)
    versions = []
    for req in requirements(ROOT / 'web/engine-requirements.txt'):
        if evalscope_min and req.name.lower() == 'evalscope':
            # Dataset-driven requirement: displayed and matched against the selection,
            # but gated by the dedicated version-compatibility item, not here.
            try:
                installed = metadata.version(req.name)
            except metadata.PackageNotFoundError:
                installed = None
            from packaging.version import Version
            matches = bool(installed) and Version(installed) >= Version(evalscope_min)
            versions.append({'name': req.name, 'installed': installed,
                             'required': f'>= {evalscope_min}（所选数据集要求）',
                             'install_constraint': str(req.specifier), 'matches': matches, 'informational': True})
            continue
        versions.append(package_version(req, native.get(req.name.lower(), ())))
    result = {'ready': all(v['matches'] for v in versions if not v.get('informational')), 'versions': versions}
    if result['ready']:
        try:
            for name in ('evalscope', 'openai', 'modelscope', 'transformers', 'pyarrow', 'openpyxl'):
                import_module(name)
        except Exception as exc:
            result.update(ready=False, error_details=f'{type(exc).__name__}: {exc}')
    return result


def check_optional(requirement, module):
    if requirement.startswith('git+'):
        # The native tau2 requirement pins a source tag, which need not equal its package version.
        packages = metadata.packages_distributions().get(module.split('.')[0], [])
        versions = []
        for name in packages:
            dist = metadata.distribution(name)
            source = json.loads(dist.read_text('direct_url.json') or '{}')
            expected_url, expected_tag = requirement.removeprefix('git+').rsplit('@', 1)
            revision = source.get('vcs_info', {}).get('requested_revision', '')
            matches = source.get('url', '').removesuffix('.git') == expected_url.removesuffix('.git') and revision == expected_tag
            archive_url = expected_url.removesuffix('.git') + '/archive/refs/tags/' + quote(expected_tag, safe='') + '.zip'
            digest = source.get('archive_info', {}).get('hashes', {}).get('sha256', '')
            if source.get('url') == archive_url and re.fullmatch(r'[a-f0-9]{64}', digest):
                revision, matches = expected_tag, True
            versions.append({'name': name, 'installed': dist.version, 'required': expected_tag,
                             'source_revision': revision or '未确认', 'matches': matches})
        if not versions:
            versions = [{'name': module, 'installed': None, 'required': requirement.rsplit('@', 1)[-1], 'matches': False}]
    else:
        versions = [package_version(Requirement(requirement))]
    result = {'ready': all(v['matches'] for v in versions), 'versions': versions}
    if result['ready']:
        try:
            import_module(module)
        except Exception as exc:
            result.update(ready=False, error_details=f'{type(exc).__name__}: {exc}')
    return result


if __name__ == '__main__':
    mode, output, *args = sys.argv[1:]
    try:
        if mode == 'core':
            evalscope_min = args[0] if args else None
            result = check_core(evalscope_min=evalscope_min)
        else:
            result = check_optional(*args)
    except Exception as exc:
        result = {'ready': False, 'versions': [], 'error_details': f'{type(exc).__name__}: {exc}'}
    Path(output).write_text(json.dumps(result, ensure_ascii=False))
    for row in result['versions']:
        print(f"{row['name']}: 框架要求 {row['required']} / 本机 {row['installed'] or '未安装'} / {'符合' if row['matches'] else '不符合'}")
    if result.get('error_details'):
        print(result['error_details'])
    raise SystemExit(0 if result['ready'] else 1)
