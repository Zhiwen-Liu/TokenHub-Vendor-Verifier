"""One bounded inference request, using the same endpoint/protocol as a run."""
import json
import time

import httpx

from .engine import endpoint


def check(url, model, protocol, key=''):
    address = endpoint(url, protocol)
    headers = {'Content-Type': 'application/json'}
    if protocol == 'anthropic':
        headers['anthropic-version'] = '2023-06-01'
        if key:
            headers['x-api-key'] = key
    elif key:
        headers['Authorization'] = 'Bearer ' + key
    payload = {'model': model, 'messages': [{'role': 'user', 'content': 'Hi'}], 'max_tokens': 1, 'stream': False}
    started = time.monotonic()
    result = {'ready': False, 'url': address, 'http_status': None}
    try:
        with httpx.Client(timeout=httpx.Timeout(10, connect=5), follow_redirects=False, trust_env=False) as client:
            with client.stream('POST', address, headers=headers, json=payload) as response:
                result['http_status'] = response.status_code
                raw = b''
                for chunk in response.iter_bytes():
                    raw += chunk
                    if len(raw) > 65536 or time.monotonic() - started > 12:
                        raise ValueError('响应过大或耗时过长，请检查服务是否返回了正确的模型响应')
                try:
                    data = json.loads(raw)
                except (ValueError, UnicodeError):
                    data = {}
                if not 200 <= response.status_code < 300:
                    reasons = {401: '认证失败，请检查 API 密钥', 403: '没有访问权限', 404: '接口路径或模型不存在', 429: '服务限流或额度不足'}
                    message = reasons.get(response.status_code, '模型请求未通过，请检查协议、模型名和服务状态')
                    # Only the API's error message is useful; never echo request headers/body.
                    error = data.get('error', {}) if isinstance(data, dict) else {}
                    detail = error.get('message', '') if isinstance(error, dict) else ''
                    result['message'] = f'HTTP {response.status_code} · {message}' + (f'：{str(detail)[:500]}' if detail else '')
                elif isinstance(data, dict) and not data.get('error') and (
                    (protocol == 'openai' and isinstance(data.get('choices'), list) and bool(data['choices']))
                    or (protocol == 'anthropic' and data.get('type') == 'message' and isinstance(data.get('content'), list))
                ):
                    result.update(ready=True, message='模型调用成功，接口、鉴权与协议检查通过')
                else:
                    result['message'] = '服务有响应，但未返回所选协议的模型结果（可能是错误的 URL 或网关页面）'
    except httpx.TimeoutException:
        result['message'] = '请求超时，请检查服务地址、网络和模型加载状态'
    except httpx.ConnectError:
        result['message'] = '无法建立连接，请检查地址、端口、服务状态和证书'
    except (httpx.HTTPError, ValueError) as exc:
        result['message'] = str(exc)
    if key:
        result['message'] = result['message'].replace(key, '[REDACTED]').replace(json.dumps(key)[1:-1], '[REDACTED]')
    result['elapsed_ms'] = round((time.monotonic() - started) * 1000)
    return result
