"""후보정(v0.1.20) 점검: 편집 · 음향 필터(ffmpeg) · 악기(가짜 분리) · 고치기(가짜 엔진). 성공하면 'OK test_post'."""
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

from dtmc import engine, ff, jobs, log, post as PO  # noqa: E402

log.setup()
app = QCoreApplication(sys.argv)
tmp = Path(tempfile.mkdtemp(prefix="dtmc_post_"))
src = ff.make_test_tone(tmp / "CHL_001_Dive_XL_20s.wav", 20, 120)


def run_job(job):
    res, items = {}, []
    loop = QEventLoop()
    job.finished_ok.connect(lambda ok, msg: (res.update(ok=ok, msg=msg), loop.quit()))
    job.item.connect(items.append)
    job.start()
    QTimer.singleShot(120000, loop.quit)
    loop.exec()
    job.wait(5000)
    return res, items


# 1. 필터 하나하나(ffmpeg 이 받아들이는지)
w = PO.to_wav(src, tmp / "a.wav")
full = PO.Post(denoise="mid", clean=["low", "high", "click", "loud"], space="water")
assert PO.sound_chain(PO.Post()) == ""
for sp in ("room", "water", "wide"):
    PO._af(w, tmp / f"s_{sp}.wav", PO.sound_chain(PO.Post(denoise="low", clean=["low", "high", "click", "loud"], space=sp)), sp)
PO.trim_silence(w, tmp / "t.wav")
d0 = ff.duration(w)
w19 = PO._af(w, tmp / "w19.wav", "atrim=0:19", "자르기")
assert abs(ff.duration(PO.end_at_bar(w19, tmp / "bar.wav", 120)) - 18.0) < 0.3     # 19초 · 2초 마디 → 9마디 = 18초
assert abs(ff.duration(PO.make_loop(w, tmp / "loop.wav", 2.0)) - (d0 - 2.0)) < 0.2
assert abs(ff.duration(PO.tempo_key(w, tmp / "tk.wav", 10, 0)) - d0 / 1.1) < 0.3
assert abs(ff.duration(PO.tempo_key(w, tmp / "k.wav", 0, 2)) - d0) < 0.3           # 조만 바꾸면 길이 그대로
PO.fades(w, tmp / "f.wav", 2, 3)
print("filters ok")

# 2. 악기: 가짜 분리(받은 소리를 4개로 복사) — 진짜 demucs 는 윈도우 실기에서
fake = tmp / "fake_stems.py"
fake.write_text("import sys, shutil\nfor n in ('vocals','drums','bass','other'):\n"
                "    shutil.copyfile(sys.argv[1], f'{sys.argv[2]}/{n}.wav')\n", encoding="utf-8")
engine.python_prefix = lambda mod: [sys.executable, str(fake)]
(engine.project()).mkdir(parents=True, exist_ok=True)
(engine.project() / ".dtmc_demucs_ok").write_text("ok", encoding="utf-8")
po = PO.Post(vocals="mute", drums="down", stems=True, cut=["trim"], fade_out=2, denoise="low", clean=["loud"])
assert po.tag() == "보컬뺌_편집_다듬기" and po.inst_on() and po.edit_on() and po.sound_on() and not po.fix_on()
src_s = jobs.Source(str(src))
job = jobs.MakeJob(engine.Engine(), [src_s], jobs.Plan(out_dir=str(tmp / "out1"), fmt="mp3"), post=po)
assert job.steps() == ["post"]
n0 = len(json.loads(urllib.request.urlopen(url + "/_requests").read()))
res, items = run_job(job)
assert res.get("ok"), res
assert len(json.loads(urllib.request.urlopen(url + "/_requests").read())) == n0      # 엔진 안 씀
names = sorted(p.name for p in (tmp / "out1").iterdir())
assert "CHL_001_Dive_XL_20s_보컬뺌_편집_다듬기.mp3" in names and "CHL_001_Dive_XL_20s_보컬.mp3" in names, names
assert len(names) == 5 and items and "보컬 뺌" in items[0]["label"]
print("inst+edit+sound ok", names)

# 3. 고치기(가짜 엔진): 부분 다시 → 다시 입히기 → 이어 만들기
po = PO.Post(repaint=True, rp_a=5, rp_b=9, redo=True, extend=True, ext_s=10)
job = jobs.MakeJob(engine.Engine(), [src_s], jobs.Plan(out_dir=str(tmp / "out2")), post=po)
assert job.steps() == ["engine", "post"]
res, items = run_job(job)
rq = json.loads(urllib.request.urlopen(url + "/_requests").read())[-3:]
assert res.get("ok"), res
assert [r["task_type"] for r in rq] == ["repaint", "cover", "repaint"], [r["task_type"] for r in rq]
assert rq[0]["repainting_start"] == 5 and rq[0]["repainting_end"] == 9 and rq[1]["audio_cover_strength"] == 1.0
assert rq[1]["cover_noise_strength"] == 0 and rq[2]["repainting_end"] > rq[2]["repainting_start"] + 9
assert Path(items[0]["path"]).name.endswith("_부분다시_다시입힘_이어.mp3")
print("fix ok")

# 4. 켠 것이 없으면 / 구간이 이상하면
res, _ = run_job(jobs.MakeJob(engine.Engine(), [src_s], jobs.Plan(out_dir=str(tmp / "o3")), post=PO.Post()))
assert res.get("ok") is False and "켠 후보정이 없습니다" in res["msg"]
res, _ = run_job(jobs.MakeJob(engine.Engine(), [src_s], jobs.Plan(out_dir=str(tmp / "o4")),
                              post=PO.Post(repaint=True, rp_a=50, rp_b=60)))
assert res.get("ok") is False and "구간이 이상합니다" in res["msg"], res
print("OK test_post")
