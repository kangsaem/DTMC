"""원곡 → 분석 → (가짜 엔진) 생성 → 5가지 형식 저장까지 화면 없이 점검. 성공하면 'OK test_pipeline' 출력."""
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

import mock_engine  # noqa: E402

srv, url = mock_engine.serve(0)
os.environ["DTMC_ENGINE_URL"] = url

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer  # noqa: E402

from dtmc import engine, ff, jobs, log, prompt  # noqa: E402

log.setup()
app = QCoreApplication(sys.argv)

def run_job(job):
    res = {}
    loop = QEventLoop()
    job.finished_ok.connect(lambda ok, msg: (res.update(ok=ok, msg=msg), loop.quit()))
    items = []
    job.item.connect(items.append)
    job.start()
    QTimer.singleShot(120000, loop.quit)
    loop.exec()
    job.wait(5000)
    return res, items


# 지시문 · 금지어 · 파일 이름
assert prompt.first_word("Tsuki ni Kawatte") == "Tsuki" and prompt.first_word("01 - Night_Drive") == "Night"
assert prompt.first_word("봄날의 산책") == "봄날의" and prompt.first_word("") == "곡"
assert [prompt.len_tag(x) for x in (30, 59.6, 90, 120, 600)] == ["30s", "1m", "1m30s", "2m", "10m"]
assert prompt.out_stem("", "Tsuki ni Kawatte", ["piano"], "XL", 60) == "Tsuki_피아노_XL_1m"
assert prompt.out_stem(" 달:밤 ", "x", ["piano", "strings"], 2, 95) == "달_밤_피아노+스트링_2_1m35s"
c = prompt.caption(["piano"], ["calm", "warm", "bright"])
assert c.startswith("Solo piano, neo-classical.") and "no drums" not in c and c.endswith("Calm, peaceful, warm, intimate mood."), c
neg = prompt.negative(["piano"])
assert "drums" in neg and "violin" in neg and "piano" not in neg, neg
assert "string ensemble" not in prompt.negative(["piano", "strings"])
r = prompt.request(instr_keys=["piano"], mood_keys=["calm"], seconds=60, bpm=110, key_scale="A major", ref_path="/r.wav")
assert r["task_type"] == "text2music" and r["vocal_language"] == "unknown" and r["lyrics"].startswith("[Intro - soft piano]")
assert r["reference_audio_path"] == "/r.wav" and r["bpm"] == 110 and r["key_scale"] == "A major"
assert r["model"] == "acestep-v15-turbo-shift3" and "src_audio_path" not in r and "audio_cover_strength" not in r
assert not hasattr(jobs.Plan(), "strength")                    # 커버(원곡 반영 %) 삭제
print("prompt ok")

tmp = Path(tempfile.mkdtemp(prefix="dtmc_pipe_"))
src = ff.make_test_tone(tmp / "원곡 테스트.wav", 20, 96)
ref1 = ff.make_test_tone(tmp / "piano ref 1.wav", 12, 80)
ref2 = ff.make_test_tone(tmp / "piano ref 2.wav", 50, 80)
rf = ff.make_ref([ref1, ref2], tmp / "ref.wav")
assert 89 < ff.duration(rf) < 91, ff.duration(rf)              # 2개 → 45초씩

for fmt in ("mp3", "flac", "m4a", "ogg", "wav"):
    for refs in ([], [str(ref1), str(ref2)]):
        s = jobs.Source(str(src))
        plan = jobs.Plan(instruments=["piano", "strings"], length_mode="set", seconds=15, refs=refs,
                         candidates=2, fmt=fmt, out_dir=str(tmp / "out"))
        job = jobs.MakeJob(engine.Engine(), [s], plan)
        res, items = run_job(job)
        assert res.get("ok"), (fmt, refs, res)
        assert len(items) == 2, items
        for it in items:
            p = Path(it["path"])
            assert p.exists() and p.suffix == ff.FORMATS[fmt][0], p
            assert p.stem.startswith(f"원곡_피아노+스트링_{it['n']}_15s"), p.name
            d = ff.duration(p)
            assert 9 < d < 16, (p, d)
        print(fmt, "refs" if refs else "no refs", "ok", [Path(i["path"]).name for i in items])

reqs = json.loads(urllib.request.urlopen(url + "/_requests").read())
assert all(r["task_type"] == "text2music" and "src_audio_path" not in r for r in reqs)     # 원곡 소리는 안 보냄
assert reqs[0]["bpm"] in (95, 96, 97) and reqs[0]["key_scale"] == "C major", reqs[0]
assert "Performed only by piano and string ensemble." in reqs[0]["prompt"], reqs[0]["prompt"]
assert "reference_audio_path" not in reqs[0] and os.path.isabs(reqs[1]["reference_audio_path"])
assert all(r["model"] == "acestep-v15-turbo-shift3" for r in reqs)          # 기본 생성 모델 = shift3
# 템포 직접(잠금 없음)
plan = jobs.Plan(instruments=["piano"], length_mode="set", seconds=10, bpm_mode="set", bpm=72, out_dir=str(tmp / "out"))
res, items = run_job(jobs.MakeJob(engine.Engine(), [jobs.Source(str(src))], plan))
assert res.get("ok") and json.loads(urllib.request.urlopen(url + "/_requests").read())[-1]["bpm"] == 72

# 비교 시험 = 설정 비교(같은 씨앗). 그래픽 메모리 24GB → shift3 기준 + XL turbo 7 + XL SFT 2 = 10곡
from dtmc import hw  # noqa: E402

real_gpu = hw.gpu
hw.gpu = lambda: hw.Gpu("시험 GPU", 24.0, "999.0")
hw._PROFILE = hw.Profile("gpu", "test", True, "acestep-5Hz-lm-1.7B", True, 2)
n0 = len(json.loads(urllib.request.urlopen(url + "/_requests").read()))
plan = jobs.Plan(instruments=["piano"], fmt="mp3", out_dir=str(tmp / "cmp"), title="Tsuki", refs=[str(ref1)])
res, items = run_job(jobs.MakeJob(engine.Engine(), [jobs.Source(str(src))], plan, compare=True))
assert res.get("ok") and len(items) == 10, (res, len(items))
names = [Path(i["path"]).name for i in items]
assert names[0] == "Tsuki_피아노_01-shift3_1m.mp3" and names[1] == "Tsuki_피아노_02-XL_지금_1m.mp3", names
assert names[-1] == "Tsuki_피아노_10-XLSFT_CFG7_1m.mp3", names
creq = json.loads(urllib.request.urlopen(url + "/_requests").read())[n0:]
M = [r["model"] for r in creq]
assert M == ["acestep-v15-turbo-shift3"] + ["acestep-v15-xl-turbo"] * 7 + ["acestep-v15-xl-sft"] * 2, M
assert len({r["seed"] for r in creq}) == 1 and all(r["audio_duration"] == 60 and r["thinking"] is True for r in creq)
assert creq[1]["lyrics"].startswith("[Intro") and "lm_cfg_scale" not in creq[1] and "dcw_enabled" not in creq[1]
assert creq[2]["lyrics"] == "[Instrumental]" and creq[2]["prompt"] == creq[1]["prompt"]
assert "unaccompanied solo piano" in creq[3]["prompt"] and creq[3]["lyrics"].startswith("[Intro")
assert creq[4]["lm_cfg_scale"] == 3.5 and creq[4]["lm_temperature"] == 0.7
assert creq[5]["lyrics"] == "[Instrumental]" and "Only piano" in creq[5]["prompt"] and creq[5]["lm_cfg_scale"] == 3.5
assert creq[6]["dcw_enabled"] is False and creq[7]["dcw_enabled"] is False and creq[7]["lyrics"] == "[Instrumental]"
assert creq[8]["inference_steps"] == 50 and creq[8]["guidance_scale"] == 4.0 and creq[9]["guidance_scale"] == 7.0
hw.gpu = real_gpu
hw._PROFILE = hw.Profile("cpu", "test", False, "", False, 1)
cs = prompt.compare_cases(jobs.xl_ok(), False)                 # XL 못 씀 · DCW 못 고침 → shift3 로 5곡
assert len(cs) == 5 and all(c[2] == prompt.GEN_MODEL for c in cs) and cs[0][0] == "01-shift3_지금", cs
hw._PROFILE = None
print("compare ok", len(names))

# 악기 시험
plan = jobs.Plan(fmt="mp3", out_dir=str(tmp / "out"))
job = jobs.MakeJob(engine.Engine(), [], plan, test=True)
res, items = run_job(job)
assert res.get("ok") and len(items) == 8, (res, len(items))
print("instrument test ok", [Path(i["path"]).name for i in items][:3], "...")

# 취소
os.environ["MOCK_DELAY"] = "30"
mock_engine.DELAY = 30
plan = jobs.Plan(length_mode="set", seconds=10, out_dir=str(tmp / "out"))
job = jobs.MakeJob(engine.Engine(), [jobs.Source(str(src))], plan)
QTimer.singleShot(2500, lambda: setattr(job, "cancel", True))
res, items = run_job(job)
assert res.get("ok") is False and "취소" in res.get("msg", ""), res
print("cancel ok")

hist = list(Path(os.environ["DTMC_USER_DIR"], "history").glob("*.jsonl"))
assert hist and hist[0].read_text(encoding="utf-8").count("\n") >= 10
print("OK test_pipeline")
