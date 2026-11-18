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

# 반영 % · A안 · 파일 이름(v0.1.6)
assert [prompt.strength_pct(x) for x in ("low", "mid", "high", 72, "85", None, 130, -5)] == [0, 30, 60, 70, 85, 70, 100, 0]
assert prompt.cover_params(0) is None
assert prompt.cover_params(30) == (0.3, 0.0) and prompt.cover_params(60) == (0.6, 0.0)
assert prompt.cover_params(70) == (0.7, 0.15) and prompt.cover_params(90) == (0.9, 0.45)
assert prompt.cover_params(100) == (1.0, 0.6)
assert jobs.Plan(strength="high").strength == 60 and jobs.Plan().strength == 70
assert prompt.first_word("Tsuki ni Kawatte") == "Tsuki" and prompt.first_word("01 - Night_Drive") == "Night"
assert prompt.first_word("봄날의 산책") == "봄날의" and prompt.first_word("") == "곡"
assert [prompt.len_tag(x) for x in (30, 59.6, 90, 120, 600)] == ["30s", "1m", "1m30s", "2m", "10m"]
assert prompt.out_stem("", "Tsuki ni Kawatte", ["piano"], "반영70", 30) == "Tsuki_피아노_반영70_30s"
assert prompt.out_stem(" 달:밤 ", "x", ["piano", "strings"], 2, 95) == "달_밤_피아노+스트링_2_1m35s"
cap = prompt.caption(["piano"], ["calm"])
assert "piano only" in cap and "no other instruments" in cap and "no drums" in cap, cap
r = prompt.request(instr_keys=["piano"], mood_keys=[], seconds=30, bpm=110, key_scale="A major", strength=70,
                   src_path="/x.wav")
assert r["task_type"] == "cover" and "bpm" not in r and "key_scale" not in r, r
assert r["audio_cover_strength"] == 0.7 and r["cover_noise_strength"] == 0.15
r = prompt.request(instr_keys=["piano"], mood_keys=[], seconds=30, bpm=110, key_scale="A major", strength=0)
assert r["task_type"] == "text2music" and r["bpm"] == 110 and r["key_scale"] == "A major"
print("prompt ok")


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


tmp = Path(tempfile.mkdtemp(prefix="dtmc_pipe_"))
src = ff.make_test_tone(tmp / "원곡 테스트.wav", 20, 96)

for fmt in ("mp3", "flac", "m4a", "ogg", "wav"):
    for strength in (0, "mid"):                    # "mid" = 옛 설정(→ 30%)
        s = jobs.Source(str(src))
        plan = jobs.Plan(instruments=["piano", "strings"], length_mode="set", seconds=15, strength=strength,
                         candidates=2, fmt=fmt, out_dir=str(tmp / "out"))
        job = jobs.MakeJob(engine.Engine(), [s], plan)
        res, items = run_job(job)
        assert res.get("ok"), (fmt, strength, res)
        assert len(items) == 2, items
        for it in items:
            p = Path(it["path"])
            assert p.exists() and p.suffix == ff.FORMATS[fmt][0], p
            assert p.stem.startswith(f"원곡_피아노+스트링_{it['n']}_15s"), p.name
            d = ff.duration(p)
            assert 9 < d < 16, (p, d)
        print(fmt, strength, "ok", [Path(i["path"]).name for i in items])

reqs = json.loads(urllib.request.urlopen(url + "/_requests").read())
low = [r for r in reqs if r["task_type"] == "text2music"]
cov = [r for r in reqs if r["task_type"] == "cover"]
assert low and cov
assert low[0]["bpm"] in (95, 96, 97), low[0]["bpm"]
assert low[0]["key_scale"] == "C major", low[0]
assert "piano and string ensemble" in low[0]["prompt"], low[0]["prompt"]
assert cov[0]["audio_cover_strength"] == 0.3 and cov[0]["cover_noise_strength"] == 0.0
assert os.path.isabs(cov[0]["src_audio_path"]) and "bpm" not in cov[0] and "key_scale" not in cov[0], cov[0]
assert all(r["lyrics"] == "[Instrumental]" for r in reqs)

# 새 지시문(v0.1.7)
c2 = prompt.caption2(["piano"], ["calm", "warm", "bright"])
assert c2.startswith("Solo piano, neo-classical.") and "no drums" not in c2 and "Calm, peaceful, warm, intimate mood." in c2, c2
assert "bright" not in c2                                     # 분위기는 2개까지
neg = prompt.negative2(["piano"])
assert "drums" in neg and "violin" in neg and "piano" not in neg.replace("pianist", ""), neg
assert prompt.mood_words("A warm and nostalgic orchestral pop track with strings and drums, hopeful") == ["warm", "nostalgic", "hopeful"]
r2 = prompt.request2(instr_keys=["piano"], mood_keys=["calm"], seconds=60, bpm=110, key_scale="A major", ref_path="/r.wav")
assert r2["task_type"] == "text2music" and r2["vocal_language"] == "unknown" and r2["lyrics"].startswith("[Intro - soft piano]")
assert r2["reference_audio_path"] == "/r.wav" and r2["bpm"] == 110 and "src_audio_path" not in r2

# 상한 확인 시험: 60초 × (지금 / 새 지시문 / + 음색 참고 / + 원곡 이해), 같은 씨앗 — 언어 모델 있는 GPU 로 가정
from dtmc import hw  # noqa: E402

hw._PROFILE = hw.Profile("gpu", "test", True, "acestep-5Hz-lm-1.7B", True, 2)
n0 = len(reqs)
ref = ff.make_test_tone(tmp / "piano ref.wav", 12, 80)
plan = jobs.Plan(instruments=["piano"], fmt="mp3", out_dir=str(tmp / "cmp"), title="Tsuki", ref_audio=str(ref))
job = jobs.MakeJob(engine.Engine(), [jobs.Source(str(src))], plan, compare=True)
res, items = run_job(job)
assert res.get("ok") and len(items) == 4, (res, items)
names = [Path(i["path"]).name for i in items]
assert names == [f"Tsuki_피아노_{x}_1m.mp3" for x in ("시험1_지금", "시험2_새지시문", "시험3_음색참고", "시험4_원곡이해")], names
creq = json.loads(urllib.request.urlopen(url + "/_requests").read())[n0:]
und = [r for r in creq if r.get("full_analysis_only")]
gen = [r for r in creq if not r.get("full_analysis_only")]
assert len(und) == 1 and os.path.isabs(und[0]["src_audio_path"]) and len(gen) == 4
assert all(r["task_type"] == "text2music" and "src_audio_path" not in r and r["thinking"] is True for r in gen)
assert len({r["seed"] for r in gen}) == 1 and all(r["audio_duration"] == 60 for r in gen)
assert "no other instruments" in gen[0]["prompt"] and "lm_negative_prompt" not in gen[0]
assert gen[1]["prompt"].startswith("Solo piano") and "reference_audio_path" not in gen[1]
assert os.path.isabs(gen[2]["reference_audio_path"]) and gen[3]["reference_audio_path"]
assert gen[3]["prompt"].endswith("Warm, nostalgic, hopeful mood."), gen[3]["prompt"]     # 원곡 이해의 분위기 단어만(악기 이름 X)
assert all(i["label"] and i["strength"] == 0 for i in items)
# 음색 참고 없음 · 언어 모델 없음 → 2곡
hw._PROFILE = hw.Profile("cpu", "test", False, "", False, 1)
plan = jobs.Plan(instruments=["piano"], fmt="mp3", out_dir=str(tmp / "cmp2"))
res, items = run_job(jobs.MakeJob(engine.Engine(), [jobs.Source(str(src))], plan, compare=True))
assert res.get("ok") and len(items) == 2, (res, items)
hw._PROFILE = None
print("compare ok", names)

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
