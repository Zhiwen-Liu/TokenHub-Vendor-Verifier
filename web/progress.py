"""Read bounded native log output; percentages describe the current stage only."""
from datetime import datetime, timezone
import math
import re


ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
COUNTER = re.compile(r'(Evaluating|Reviewing|Scoring|Processing|Warmup|Loading|Generating)\[([^\]\r\n]{1,160})\](.*)')
STAGES = {'Evaluating': ('推理与评分', '条'), 'Reviewing': ('评审', '条'),
          'Scoring': ('评分', '条'), 'Processing': ('压测请求', '次'),
          'Warmup': ('预热', '次'), 'Loading': ('加载数据', '条'),
          'Generating': ('生成压测请求', '条')}


def parse_progress(spec, text):
    result = {'stage': '启动评测框架', 'context': '', 'completed': None, 'total': None,
              'percent': None, 'unit': ''}
    dataset, bucket = '', ''

    def stage(name, context=''):
        result.update(stage=name, context=context, completed=None, total=None, percent=None, unit='')

    for line in ANSI.sub('', text).splitlines():
        ds = re.search(r'^\s*数据集:\s*(\S+)', line)
        bench = re.search(r'\[bench\].*\bbucket=(\S+)\s+parallel=(\d+)\s+number=(\d+)', line)
        if ds:
            dataset = ds[1]
            stage('加载数据集', dataset)
        elif bench:
            bucket = f'{bench[1]} · 并发 {bench[2]}'
            stage('准备压测', bucket)
        elif re.search(r'等待\s*\d+s\s*后重试|第\s*\d+/\d+\s*次尝试', line):
            stage('等待重试 / 重新执行', dataset or bucket)
        elif 'bucket cooldown' in line:
            stage('档位间冷却', bucket)
        elif '[report]' in line or '结果打包完成' in line or '评测完成!' in line or '[done] 结果目录:' in line:
            stage('整理结果与报告', dataset or bucket)
        match = COUNTER.search(line)
        if not match:
            continue
        kind, label, remainder = match.groups()
        if kind == 'Evaluating':
            dataset = label
        context = dataset if spec['mode'] == 'eval' else bucket
        if spec['mode'] == 'eval' and dataset in spec.get('datasets', []):
            context = f"{dataset} · 数据集 {spec['datasets'].index(dataset) + 1}/{len(spec['datasets'])}"
        if spec['mode'] == 'perf' and not context:
            parallel = re.search(r'parallel_(\d+)', label)
            context = '并发 ' + parallel[1] if parallel else label
        stage(STAGES[kind][0], context)
        result['unit'] = STAGES[kind][1]
        count = re.search(r'\b(\d[\d,]*)\s*/\s*(\d[\d,]*)\b', remainder)
        if count:
            completed, total = (int(v.replace(',', '')) for v in count.groups())
            if total > 0 and 0 <= completed <= total:
                # Native text rounds 593/594 to 100%; use the actual counts instead.
                result.update(completed=completed, total=total,
                              percent=math.floor(1000 * completed / total) / 10)
    return result


def task_progress(task, folder):
    status = task['status']
    text = ''
    if status not in ('queued', 'succeeded'):
        try:
            with (folder / 'run.log').open('rb') as file:
                file.seek(0, 2)
                file.seek(max(0, file.tell() - 128_000))
                text = file.read().decode(errors='replace')
        except FileNotFoundError:
            pass
    result = parse_progress(task['spec'], text)
    if status == 'queued':
        result.update(stage='等待任务启动', percent=None)
    elif status == 'succeeded':
        result.update(stage='测试完成', percent=100)
    elif status in ('failed', 'interrupted'):
        result['stage'] = ('测试失败' if status == 'failed' else '任务已中断') + ' · ' + result['stage']
    result['active'] = status in ('queued', 'running')
    result['elapsed_seconds'] = 0
    if task.get('started'):
        try:
            start = datetime.fromisoformat(task['started'])
            end = datetime.fromisoformat(task['finished']) if task.get('finished') else datetime.now(timezone.utc)
            result['elapsed_seconds'] = max(0, int((end - start).total_seconds()))
        except (ValueError, TypeError):
            pass
    return result
