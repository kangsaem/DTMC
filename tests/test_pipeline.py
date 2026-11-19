"""원곡 → 분석 → (가짜 엔진) 생성 → 5가지 형식 저장까지 화면 없이 점검. 성공하면 'OK test_pipeline' 출력."""
import json
import re
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
assert jobs.Plan().strength == 0                                 # 원곡 반영(v0.1.18 다시 넣음) 기본 = 안 씀
print("prompt ok")

# 스타일 12 · 파일 이름(v0.1.12)
assert len(prompt.STYLES) == 12 and {"anime", "hardrock"} <= set(prompt.STYLE_BY)
assert prompt.stem("", "", "칠아웃", 1, 60) == "칠아웃_1_1m" and prompt.stem("", "Love Good", "하드록", 2, 95) == "Love_하드록_2_1m35s"
assert prompt.stem(" 달:밤 ", "x", "로파이", 1, 30) == "달_밤_로파이_1_30s"
assert prompt.name("chill", 7, "Dive", "", "", 90) == "CHL_007_Dive_1m30s" and prompt.name("anime", 8, "", "", "보컬-지금", 30) == "ANI_008_보컬-지금_30s"
assert prompt.name("hardrock", 12, "", "Love Good.wav", "", 60) == "ROK_012_Love_1m"
_p = {k: prompt.preset(q, True) for k, q in prompt.ENGINE_QUALITY.items()}       # 엔진 버튼 → 조합(v0.1.17)
assert _p["1.7B"]["model"] == prompt.GEN_MODEL and "ACESTEP_LM_MODEL_PATH" not in _p["1.7B"]["env"]
assert _p["4B"]["model"] == prompt.GEN_MODEL and _p["4B"]["env"]["ACESTEP_LM_MODEL_PATH"] == prompt.LM_4B
assert _p["XL"]["model"] == prompt.XL_TURBO and "ACESTEP_LM_MODEL_PATH" not in _p["XL"]["env"] and _p["XL"]["opt"] == prompt.XL_OPT
assert all("ACESTEP_VAE_CHECKPOINT" in v["env"] for v in _p.values())
_a, _b = jobs.next_seq("pop"), jobs.next_seq("pop")
assert _b == _a + 1 and jobs.next_seq("jazz") >= 1
sr = prompt.style_request("anime", seconds=30, bpm=110, key_scale="A major")
assert "bpm" not in sr and sr["key_scale"] == "A major" and sr["prompt"].startswith("J-Rock anime opening")   # 110 = 범위 밖 → 엔진이 정함
assert prompt.style_request("anime", seconds=30, bpm=110, force_bpm=True)["bpm"] == 110                        # 직접 정하면 그대로
sr = prompt.style_request("hardrock", seconds=30, bpm=120, caption="  my own prompt  ", candidates=3)
assert sr["prompt"] == "my own prompt" and sr["bpm"] == 120 and sr["batch_size"] == 3 and "vocals" in sr["lm_negative_prompt"]
assert sr["task_type"] == "text2music" and sr["model"] == prompt.GEN_MODEL and sr["lyrics"] == prompt.STYLE_LYRICS

tmp = Path(tempfile.mkdtemp(prefix="dtmc_pipe_"))
src = ff.make_test_tone(tmp / "원곡 테스트.wav", 20, 96)
ref1 = ff.make_test_tone(tmp / "piano ref 1.wav", 12, 80)
ref2 = ff.make_test_tone(tmp / "piano ref 2.wav", 50, 80)
rf = ff.make_ref([ref1, ref2], tmp / "ref.wav")
assert 89 < ff.duration(rf) < 91, ff.duration(rf)              # 2개 → 45초씩


def last_reqs(n=1):
    return json.loads(urllib.request.urlopen(url + "/_requests").read())[-n:]


for fmt in ("mp3", "flac", "m4a", "ogg", "wav"):
    for refs in ([], [str(ref1), str(ref2)]):
        s = jobs.Source(str(src))
        plan = jobs.Plan(style="chill", length_mode="set", seconds=15, refs=refs, candidates=2, fmt=fmt,
                         out_dir=str(tmp / "out"))
        job = jobs.MakeJob(engine.Engine(), [s], plan)
        res, items = run_job(job)
        assert res.get("ok"), (fmt, refs, res)
        assert len(items) == 2, items
        for it in items:
            p = Path(it["path"])
            assert p.exists() and p.suffix == ff.FORMATS[fmt][0], p
            assert re.fullmatch(r"CHL_\d{3}_원곡_[^_]+_15s", p.stem), p.name
            d = ff.duration(p)
            assert 9 < d < 16, (p, d)
        print(fmt, "refs" if refs else "no refs", "ok", [Path(i["path"]).name for i in items])

reqs = json.loads(urllib.request.urlopen(url + "/_requests").read())
assert all(r["task_type"] == "text2music" and "src_audio_path" not in r for r in reqs)     # 원곡 소리는 커버로 안 보냄
assert reqs[0]["bpm"] in (95, 96, 97) and reqs[0]["key_scale"] == "C major", reqs[0]      # 96 = 칠아웃 범위 안
assert reqs[0]["prompt"].startswith("Chillout") and "reference_audio_path" not in reqs[0]
assert os.path.isabs(reqs[1]["reference_audio_path"])
assert all(r["model"] == "acestep-v15-turbo-shift3" for r in reqs)          # 기본 생성 모델 = shift3
# 템포 직접 · 지시문 직접 고침
plan = jobs.Plan(style="lofi", caption="Slow sea turtle over coral, gentle lo-fi.", length_mode="set", seconds=10,
                 bpm_mode="set", bpm=72, out_dir=str(tmp / "out"))
res, items = run_job(jobs.MakeJob(engine.Engine(), [jobs.Source(str(src))], plan))
r = last_reqs()[0]
assert res.get("ok") and r["bpm"] == 72 and r["prompt"].startswith("Slow sea turtle") and "직접 고침" in items[0]["label"]
# 원곡 느낌 참고 = 원곡을 음색 참고로(커버 아님)
plan = jobs.Plan(style="cinematic", src_feel=True, refs=[str(ref1)], length_mode="set", seconds=10, out_dir=str(tmp / "out"))
res, items = run_job(jobs.MakeJob(engine.Engine(), [jobs.Source(str(src))], plan))
r = last_reqs()[0]
assert res.get("ok") and os.path.isabs(r["reference_audio_path"]) and r["task_type"] == "text2music" and "src_audio_path" not in r
# 원곡 없이: 스타일 · 길이만
plan = jobs.Plan(style="hardrock", length_mode="set", seconds=12, candidates=1, out_dir=str(tmp / "nosrc"))
res, items = run_job(jobs.MakeJob(engine.Engine(), [], plan))
r = last_reqs()[0]
assert res.get("ok") and len(items) == 1 and re.fullmatch(r"ROK_\d{3}_[^_]+_12s\.mp3", Path(items[0]["path"]).name), (res, items)
assert "bpm" not in r and "key_scale" not in r and r["prompt"].startswith("Hard rock") and items[0]["src_path"] == ""
# 분석 끔 = 원곡이 있어도 안 씀
plan = jobs.Plan(style="chill", analyze_on=False, src_feel=True, length_mode="set", seconds=10, out_dir=str(tmp / "out"))
res, items = run_job(jobs.MakeJob(engine.Engine(), [jobs.Source(str(src))], plan))
r = last_reqs()[0]
assert res.get("ok") and "bpm" not in r and "key_scale" not in r and "reference_audio_path" not in r

# 큰 GPU = 작곡 4B + 대체 변환부 환경 · 그 밖 = 대체 변환부만(XL 선택은 뺌)
from dtmc import hw  # noqa: E402

ps = prompt.preset("std", True)
assert ps["model"] == prompt.GEN_MODEL and ps["opt"] == {} and ps["env"] == {**prompt.ENV_VAE, **prompt.ENV_LM4B}
assert prompt.preset("std", False)["env"] == prompt.ENV_VAE
e = engine.Engine()
assert e.server_env(prompt.ENV_VAE)["ACESTEP_VAE_CHECKPOINT"] == "scragvae" and "ACESTEP_VAE_CHECKPOINT" not in e.server_env()
real_gpu = hw.gpu
hw.gpu = lambda: hw.Gpu("시험 GPU", 24.0, "999.0")
hw._PROFILE = hw.Profile("gpu", "test", True, "acestep-5Hz-lm-1.7B", True, 2)
job = jobs.MakeJob(engine.Engine(), [jobs.Source(str(src))], jobs.Plan(length_mode="set", seconds=10, out_dir=str(tmp / "big")))
res, items = run_job(job)
r = last_reqs()[0]
assert res.get("ok") and r["model"] == prompt.GEN_MODEL and "dcw_enabled" not in r and r["thinking"] is True
assert job.senv == {**prompt.ENV_VAE, **prompt.ENV_LM4B}, job.senv

# 스타일 샘플 12곡(같은 씨앗) — 원곡 있음 / 없음
n0 = len(json.loads(urllib.request.urlopen(url + "/_requests").read()))
plan = jobs.Plan(fmt="mp3", out_dir=str(tmp / "cmp"), title="Dive")
res, items = run_job(jobs.MakeJob(engine.Engine(), [jobs.Source(str(src))], plan, compare=True))
assert res.get("ok") and len(items) == 12, (res, len(items))
names = [Path(i["path"]).name for i in items]
assert re.fullmatch(r"AMB_\d{3}_Dive_샘플_[^_]+_1m\.mp3", names[0]) and re.fullmatch(r"ROK_\d{3}_Dive_샘플_[^_]+_1m\.mp3", names[-1]), names
creq = json.loads(urllib.request.urlopen(url + "/_requests").read())[n0:]
assert len({r["seed"] for r in creq}) == 1 and len({r["prompt"] for r in creq}) == 12
assert all(r["key_scale"] == "C major" and r["model"] == prompt.GEN_MODEL for r in creq)
assert "bpm" not in creq[0] and creq[3]["bpm"] in (95, 96, 97) and "bpm" not in creq[10]   # 템포 = 스타일 범위 안일 때만
res, items = run_job(jobs.MakeJob(engine.Engine(), [], jobs.Plan(out_dir=str(tmp / "cmp2")), compare=True))
assert res.get("ok") and len(items) == 12 and re.fullmatch(r"AMB_\d{3}_샘플_[^_]+_1m\.mp3", Path(items[0]["path"]).name)
hw.gpu = real_gpu
hw._PROFILE = None
print("style samples ok", len(names))

# 원곡 길이 맞추기(v0.1.13): 마디 단위 재배치 계획 · 편집 · 이음매 다시 그리기 · 커버
from dtmc import fit  # noqa: E402

long_src = ff.make_test_tone(tmp / "Long Song.wav", 64, 120)          # 마디 2초 × 32
f = fit.plan_file(long_src, 120, 40)
assert f.changed() and abs(f.length - 40) < 1.1 and len(f.segments) == 2 and len(f.seams) == 1, f
assert all(abs(((b - a) / f.bar) - round((b - a) / f.bar)) < 1e-3 for a, b in f.segments[1:-1]) and f.bar == 2.0
f2 = fit.plan_file(long_src, 120, 90)
assert abs(f2.length - 90) < 1.1 and len(f2.segments) == 3 and len(f2.seams) == 1, f2      # 한 묶음을 한 번 더 — 건너뛰는 자리는 하나
assert not fit.plan_file(long_src, 120, 64).changed()
w0 = fit.repaint_windows(f)
assert len(w0) == 1 and 3 <= w0[0][1] - w0[0][0] <= 20 and w0[0][0] < f.seams[0] < w0[0][1]
arr = ff.arrange(long_src, f.segments, tmp / "arr.wav")
assert abs(ff.duration(arr) - f.length) < 0.1
hw.gpu = lambda: hw.Gpu("시험 GPU", 24.0, "999.0")
hw._PROFILE = hw.Profile("gpu", "test", True, "acestep-5Hz-lm-1.7B", True, 2)
n0 = len(json.loads(urllib.request.urlopen(url + "/_requests").read()))
plan = jobs.Plan(style="jazz", length_mode="set", seconds=40, out_dir=str(tmp / "fit"), title="Dive")
res, items = run_job(jobs.MakeJob(engine.Engine(), [jobs.Source(str(long_src))], plan, fit_len=True))
assert res.get("ok") and len(items) == 4, (res, items)
names = [Path(i["path"]).name for i in items]
assert [re.sub(r"^JAZ_\d{3}_", "", x) for x in names] == ["Dive_길이맞춤1-편집만_40s.mp3", "Dive_길이맞춤2-이음매_shift3_40s.mp3",
                                                         "Dive_길이맞춤3-커버100_shift3_40s.mp3", "Dive_길이맞춤4-커버70_재즈 트리오_shift3_40s.mp3"], names
assert abs(ff.duration(items[0]["path"]) - 40) < 0.2                        # 편집만 = 실제 소리 길이
fr = json.loads(urllib.request.urlopen(url + "/_requests").read())[n0:]
assert [("und" if r.get("full_analysis_only") else r["task_type"]) for r in fr] == ["und", "repaint", "cover", "cover"], fr
rp, c1, c2 = fr[1], fr[2], fr[3]
assert rp["chunk_mask_mode"] == "explicit" and rp["repainting_start"] < f.seams[0] < rp["repainting_end"] and rp["thinking"] is False
assert rp["prompt"].startswith("A warm and nostalgic")                       # 원곡 묘사(가짜 엔진의 글)
assert c1["audio_cover_strength"] == 1.0 and c1["cover_noise_strength"] == 0.6 and c1["prompt"] == rp["prompt"]
assert c2["audio_cover_strength"] == 0.7 and c2["cover_noise_strength"] == 0.15 and c2["prompt"].startswith("Jazz trio")
assert all(os.path.isabs(r["src_audio_path"]) and "bpm" not in r for r in fr[1:])
res, items = run_job(jobs.MakeJob(engine.Engine(), [jobs.Source(str(long_src))],
                                  jobs.Plan(length_mode="set", seconds=64, out_dir=str(tmp / "fit")), fit_len=True))
assert res.get("ok") is False and "바꿀 것이 없습니다" in res.get("msg", ""), res      # 같은 길이 = 할 일 없음
hw.gpu = real_gpu
hw._PROFILE = None
print("fit ok", names)

# v0.1.18: 곡 수 채우기 · 원곡 반영(커버) · 다시 입히기 · 파일 이름 엔진 표기
os.environ["MOCK_VRAM_CAP"] = "3"
n0 = len(json.loads(urllib.request.urlopen(url + "/_requests").read()))
res, items = run_job(jobs.MakeJob(engine.Engine(), [], jobs.Plan(length_mode="set", seconds=10, candidates=4,
                                                                  out_dir=str(tmp / "fill"))))
os.environ.pop("MOCK_VRAM_CAP")
rq = json.loads(urllib.request.urlopen(url + "/_requests").read())[n0:]
assert res.get("ok") and len(items) == 4 and [r["batch_size"] for r in rq] == [4, 1], (res, [r["batch_size"] for r in rq])
assert "seed" not in rq[1] and "곡 수 모자람" not in res["msg"]
assert all(re.search(r"_\d{3}_(4B|1\.7B|0\.6B|noLM)_10s\.mp3$", Path(i["path"]).name) for i in items), [Path(i["path"]).name for i in items]
n0 = len(json.loads(urllib.request.urlopen(url + "/_requests").read()))
res, items = run_job(jobs.MakeJob(engine.Engine(), [jobs.Source(str(src))],
                                  jobs.Plan(strength=70, candidates=2, out_dir=str(tmp / "cov"), title="Dive")))
r = json.loads(urllib.request.urlopen(url + "/_requests").read())[n0:][-1]
assert res.get("ok") and len(items) == 2 and r["task_type"] == "cover" and r["audio_cover_strength"] == 0.7, (res, r)
assert r["cover_noise_strength"] == 0.15 and r["batch_size"] == 2 and r["thinking"] is False and "bpm" not in r
assert re.fullmatch(r"CHL_\d{3}_Dive_반영70_shift3_20s\.mp3", Path(items[0]["path"]).name), items[0]["path"]
assert prompt.cover_params(50) == (0.5, 0.0) and prompt.cover_params(100) == (1.0, 0.6)
redo_src = items[0]["path"]
n0 = len(json.loads(urllib.request.urlopen(url + "/_requests").read()))
res, items = run_job(jobs.MakeJob(engine.Engine(), [], jobs.Plan(out_dir=str(tmp / "redo"), title="Dive"), redo=redo_src))
rq = json.loads(urllib.request.urlopen(url + "/_requests").read())[n0:]
assert res.get("ok") and len(items) == 3 and all(x["task_type"] == "cover" and x["cover_noise_strength"] == 0 for x in rq), res
assert len({x["seed"] for x in rq}) == 1 and prompt.REAL_PHRASE in rq[1]["prompt"] and prompt.REAL_PHRASE not in rq[0]["prompt"]
assert [x["audio_cover_strength"] for x in rq] == [1.0, 1.0, 0.7] and "입히기1-shift3-100" in Path(items[0]["path"]).name
print("v0.1.18 ok")

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
