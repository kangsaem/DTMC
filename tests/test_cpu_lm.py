"""CPU(노트북) 작곡 담당 선택(v0.1.14) 점검. 성공하면 'OK test_cpu_lm' 출력."""
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["DTMC_USER_DIR"] = tempfile.mkdtemp(prefix="dtmc_user_")
os.environ["DTMC_ENGINE_DIR"] = tempfile.mkdtemp(prefix="dtmc_engine_")
os.environ["DTMC_FORCE_CPU"] = "1"

import mock_engine  # noqa: E402

srv, url = mock_engine.serve(0)
os.environ["DTMC_ENGINE_URL"] = url

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer  # noqa: E402

from dtmc import engine, hw, jobs, log, prompt  # noqa: E402

log.setup()
app = QCoreApplication(sys.argv)
assert hw.profile().mode == "cpu" and not hw.profile().thinking


def run_job(job):
    res = {}
    loop = QEventLoop()
    job.finished_ok.connect(lambda ok, msg: (res.update(ok=ok, msg=msg), loop.quit()))
    job.start()
    QTimer.singleShot(120000, loop.quit)
    loop.exec()
    job.wait(5000)
    return res


def last_req():
    return json.loads(urllib.request.urlopen(url + "/_requests").read())[-1]


out = tempfile.mkdtemp(prefix="dtmc_out_")
# 1. 가짜 엔진: 0.6B 고르면 thinking 켬 · 끔이면 끔
for k, think in (("0.6B", True), ("off", False), ("1.7B", True)):
    plan = jobs.Plan(analyze_on=False, length_mode="set", seconds=10, candidates=1, out_dir=out, cpu_lm=k)
    job = jobs.MakeJob(engine.Engine(), [], plan)
    res = run_job(job)
    assert res.get("ok"), res
    r = last_req()
    assert r["thinking"] is think, (k, r["thinking"])
    assert job.lm_key == k, (k, job.lm_key)
    assert (f"작곡 담당 {k}" in res["msg"]) == think, res["msg"]
    if think:
        assert job.senv["ACESTEP_INIT_LLM"] == "true" and job.senv["ACESTEP_LM_MODEL_PATH"] == prompt.CPU_LM_MODEL[k]
    else:
        assert "ACESTEP_INIT_LLM" not in job.senv
print("mock ok")

# 2. 실제 엔진처럼(가짜 엔진 주소 없이): 모델 준비 · 서버가 실제로 읽었는지 확인
del os.environ["DTMC_ENGINE_URL"]
ck = engine.checkpoints()
(ck / prompt.LM_17).mkdir(parents=True, exist_ok=True)
assert engine.model_ready(prompt.LM_17)                     # 기본 설치에 들어 있음 → 다시 안 받음
assert not engine.model_ready(prompt.LM_06)

eng = engine.Engine()
job = jobs.MakeJob(eng, [], jobs.Plan(cpu_lm="1.7B"))
senv = {}
assert job._cpu_lm(senv) == "1.7B" and senv["ACESTEP_INIT_LLM"] == "true"
e = eng.server_env(senv)
assert e["ACESTEP_INIT_LLM"] == "true" and e["ACESTEP_LM_MODEL_PATH"] == prompt.LM_17 and e["ACESTEP_DEVICE"] == "cpu"
assert eng.server_env({})["ACESTEP_INIT_LLM"] == "false"    # 평소(끔)

eng.lm_loaded = ""                                          # 서버가 못 읽음(메모리 부족 등) → 끈 채로
job._lm_result("1.7B")
assert job.lm_key == "off" and not job.prof.thinking
eng.lm_loaded = "acestep-5Hz-lm-1.7B"
job._lm_result("1.7B")
assert job.lm_key == "1.7B" and job.prof.thinking and job.prof.lm_ready
assert prompt.style_request("chill", seconds=10, profile=job.prof)["thinking"] is True

# 0.6B 를 못 받으면(엔진 없음) 끈 채로
job2 = jobs.MakeJob(engine.Engine(), [], jobs.Plan(cpu_lm="0.6B"))
senv = {}
assert job2._cpu_lm(senv) == "off" and senv == {}

# 속도 기억은 작곡 담당별로 따로
from dtmc import settings  # noqa: E402
for k in ("speed_cpu", "speed_cpu_0.6B", "speed_cpu_1.7B"):
    settings.put(k, None)
jobs.remember_speed(10, 50)
jobs.remember_speed(10, 120, "0.6B")
assert jobs.speed() == 5.0 and jobs.speed("0.6B") == 12.0 and jobs.speed("1.7B") is None
assert jobs.mode_key("0.6B") == "cpu_0.6B" and jobs.mode_key("off") == "cpu"
print("OK test_cpu_lm")
