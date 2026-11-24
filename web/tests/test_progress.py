import json
import sys
import time

from web.tests.auth_helpers import AuthenticatedClient as TestClient, create_app

from web import connectivity, engine
from web.environment import Environment
from web.progress import parse_progress, task_progress
from web.tests.test_web import HEADERS, add_object, spec


EVAL = {'mode': 'eval', 'datasets': ['arxivmath', 'gpqa_diamond']}
PERF = {'mode': 'perf', 'datasets': []}


def test_native_progress_counts_are_not_rounded_to_premature_completion():
    progress = parse_progress(EVAL, '[stderr] Evaluating[gpqa_diamond]: 100%|█████| 593/594 [1:35:26<00:58, 58.13s/it]\x1b[A')
    assert progress['completed'] == 593 and progress['total'] == 594
    assert progress['percent'] == 99.8
    assert progress['context'] == 'gpqa_diamond · 数据集 2/2'
    assert progress['stage'] == '推理与评分'


def test_stage_changes_and_retries_reset_counters():
    text = 'Evaluating[arxivmath] 100%| 103/103 [Elapsed: 02:00]\n  数据集: gpqa_diamond\n'
    assert parse_progress(EVAL, text)['percent'] is None
    text += 'Evaluating[gpqa_diamond] 30%| 30/100 [Elapsed: 10]\n等待 60s 后重试...\n'
    assert parse_progress(EVAL, text)['percent'] is None
    text += 'Evaluating[gpqa_diamond] 35%| 35/100 [Elapsed: 20]\n'
    assert parse_progress(EVAL, text)['percent'] == 35
    text += 'Scoring[batch] 50%| 1/2 [Elapsed: 01]\n'
    assert parse_progress(EVAL, text)['stage'] == '评分'
    assert parse_progress(EVAL, text)['percent'] == 50
    assert parse_progress(EVAL, text + '\n  ✅ 评测完成! 耗时: 50s')['percent'] is None


def test_perf_progress_distinguishes_warmup_requests_and_cooldown():
    text = '[bench] model=fixture bucket=9k parallel=10 number=100 warmup=4\nWarmup[parallel_10_number_100]: 50%|##| 2/4 [00:01]\n'
    assert parse_progress(PERF, text)['stage'] == '预热'
    text += 'Processing[parallel_10_number_100] 25%| 25/100 [Elapsed: 10]\n'
    progress = parse_progress(PERF, text)
    assert progress['context'] == '9k · 并发 10' and progress['percent'] == 25
    assert progress['unit'] == '次'
    assert parse_progress(PERF, text + 'bucket cooldown 120s before next bucket')['percent'] is None
    assert parse_progress(PERF, text + '[report] 正在生成报告')['stage'] == '整理结果与报告'


def test_unknown_totals_and_terminal_states_are_honest(tmp_path):
    assert parse_progress(PERF, 'Loading[conversations]: 120it [00:01]')['percent'] is None
    assert parse_progress(EVAL, 'Evaluating[arxivmath]: 100%| 12/10 [00:01]')['percent'] is None
    (tmp_path / 'run.log').write_text('Evaluating[arxivmath]: 20%| 2/10 [00:01]')
    task = {'spec': EVAL, 'status': 'failed', 'started': '2026-11-24T00:00:00+00:00', 'finished': '2026-11-24T00:01:05+00:00'}
    result = task_progress(task, tmp_path)
    assert result['percent'] == 20 and not result['active'] and result['elapsed_seconds'] == 65
    assert task_progress({**task, 'status': 'succeeded'}, tmp_path)['percent'] == 100
    assert task_progress({**task, 'status': 'queued'}, tmp_path)['percent'] is None


def test_running_process_reports_live_progress_and_finishes(tmp_path, monkeypatch):
    release = tmp_path / 'finish-fixture'
    monkeypatch.setattr(engine, 'readiness', lambda: {'ready': True})
    monkeypatch.setattr(Environment, 'validated', lambda *args: True)
    monkeypatch.setattr(connectivity, 'check', lambda *args: {'ready': True})
    def fake_native(task, workspace, secrets):
        workspace.mkdir(parents=True)
        script = '''
import json, pathlib, sys, time
print('Evaluating[arxivmath]: 25%|##| 1/4 [00:01]', flush=True)
deadline=time.monotonic()+8
while not pathlib.Path(sys.argv[1]).exists() and time.monotonic()<deadline: time.sleep(.02)
p=pathlib.Path('eval/results/run');p.mkdir(parents=True)
(p/'all_eval_summary.json').write_text(json.dumps({'results':[]}))
print('Evaluating[arxivmath]: 100%|####| 4/4 [00:02]', flush=True)
'''
        return [sys.executable, '-c', script, str(release)], {}
    monkeypatch.setattr(engine, 'prepare_run', fake_native)
    app = create_app(tmp_path / 'state')
    with TestClient(app, headers=HEADERS) as client:
        task = client.post('/api/tasks', json=spec(add_object(client))).json()
        deadline = time.monotonic() + 5
        try:
            while time.monotonic() < deadline:
                detail = client.get('/api/tasks/' + task['id']).json()
                if detail['progress']['percent'] == 25:
                    break
                time.sleep(.02)
            assert detail['status'] == 'running' and detail['progress']['percent'] == 25
            assert client.get('/api/tasks').json()[0]['progress']['completed'] == 1
        finally:
            release.touch()
        while time.monotonic() < deadline:
            detail = client.get('/api/tasks/' + task['id']).json()
            if detail['status'] == 'succeeded':
                break
            time.sleep(.02)
        assert detail['status'] == 'succeeded' and detail['progress']['percent'] == 100
