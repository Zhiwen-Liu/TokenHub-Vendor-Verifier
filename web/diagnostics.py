"""Short actionable messages, with technical details kept separately."""
import re


def failure_details(error, *, kind='data', preparing=False, code=None):
    raw = str(error)
    lower = raw.lower()
    resource = 'Tokenizer' if kind == 'tokenizer' else '数据集'
    if code == 'missing_cache':
        message = f'{resource}尚未在本机准备，点击“准备缺失项”下载。'
    elif code == 'timeout' or any(s in lower for s in ('timed out', 'timeout', '超时')):
        message = '准备超时，可重试并复用已下载内容。' if preparing else '本地加载检查超时，请查看详情后重试。'
    elif re.search(r'\b(401|403)\b', lower) or any(s in lower for s in ('gated', 'authenticationerror', 'permissiondeniederror', 'access denied')):
        message = '数据源需要访问授权，请先完成数据源登录或申请权限。'
    elif any(s in lower for s in ('modulenotfounderror', 'importerror', 'no module named')):
        message = '缺少所需组件，点击“准备缺失项”安装后重试。'
    elif 'no space left' in lower:
        message = '本机存储空间不足，请清理空间后重试。'
    elif any(s in lower for s in ('permissionerror', 'permission denied', 'read-only file system')):
        message = '本地目录无法读写，请检查目录权限。'
    elif any(s in lower for s in ('connectionerror', 'networkerror', 'max retries', 'name resolution', 'failed to resolve', "couldn't connect", '无法连接')):
        message = '无法连接数据源，请检查网络后重试准备。' if preparing else f'{resource}本地缓存不完整，点击“准备缺失项”补齐。'
    elif any(s in lower for s in ('filenotfounderror', 'not found in the cached', "couldn't find them in the cached", 'localentrynotfound', '本地缓存', '文件缺失', 'git lfs 指针')):
        message = f'{resource}本地文件不完整，点击“准备缺失项”补齐。'
    else:
        message = f'{resource}加载未通过，请展开详情查看原因。'
    return {'ready': False, 'detail': message, 'error_details': raw[:4000], 'error_code': code or 'load_failed'}
