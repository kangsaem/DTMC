"""화면 점검(화면 없이 offscreen): 시작 창 · 작업 창(설정·작업 중·끝) · 엔진 설치 창을 그려 ui_shots/ 에 저장.
가짜 엔진(mock_engine)으로 [AUTO] 를 실제로 눌러 결과 줄까지 확인. 성공하면 'OK test_ui_smoke'."""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["DTMC_USER_DIR"] = tempfile.mkdtemp(prefix="dtmc_user_")
os.environ["DTMC_ENGINE_DIR"] = tempfile.mkdtemp(prefix="dtmc_engine_")
os.environ["DTMC_MULTI"] = "1"
SHOTS = ROOT / "ui_shots"
SHOTS.mkdir(exist_ok=True)

import mock_engine  # noqa: E402

srv, url = mock_engine.serve(0)
mock_engine.DELAY = 1.0

from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
if sys.platform.startswith("linux"):
    app.setFont(QFont("Noto Sans KR", 9))
import main as M  # noqa: E402
from dtmc import ff, log, ui_style  # noqa: E402

log.setup()
M.dark(app)
ui_style.confirm = lambda *a, **k: "ok"        # 끝 알림 창이 시험을 멈추지 않게
ui_style.notice = lambda *a, **k: None


def pump(sec):
    t = time.monotonic() + sec
    while time.monotonic() < t:
        app.processEvents()
        time.sleep(0.02)


def shot(w, name):
    pump(0.2)
    w.grab().save(str(SHOTS / name))


tmp = Path(tempfile.mkdtemp(prefix="dtmc_ui_"))
a1 = ff.make_test_tone(tmp / "봄날의 산책.wav", 40, 96)
a2 = ff.make_test_tone(tmp / "Night Drive.wav", 25, 128)

a = M.App(app)
a.start.show()
shot(a.start, "01_start.png")

# 설치 전 화면(엔진 없음) — 설치 창
from dtmc.ui_engine import EngineDialog  # noqa: E402

dlg = EngineDialog()
dlg.show()
shot(dlg, "02_engine_dialog.png")
dlg.close()

# 원곡 없이도 설정 화면 · [AUTO] 가능
a.open_work([])
w = a.work
pump(0.3)
assert w.size().width() == 982 and w.size().height() == 720, w.size()
assert all(sr.body.isVisible() for k, sr in w.stages.items() if k in ("analyze", "generate", "output"))               # 세 단계 늘 다 펼침(DTME v0.17.2 틀)
assert w.b_start.isEnabled() and "원곡 없이" in w.sum.text() and not w.len_mode.isEnabled()
assert w.plan().length_mode == "set" and w.plan().style == "chill"
shot(w, "03_work_empty.png")

a.open_work([str(a1), str(a2)])
for _ in range(200):
    pump(0.05)
    if all(s.analysis is not None for s in w.sources):
        break
assert all(s.analysis is not None for s in w.sources), [s.error for s in w.sources]
assert 90 < w.sources[0].analysis.bpm < 102, w.sources[0].analysis.bpm
assert len(w.style.btns) == 12 and not hasattr(w, "instr") and not hasattr(w, "quality")
w.cand.set_value("2")
w._sync()
assert w.len_mode.isEnabled() and w.cap.toPlainText().startswith("Chillout") and "자동" in w.cap_note.text()
shot(w, "04_work_setup.png")
w.strength.setValue(12)
assert w.plan().strength == 60 and w.strength_lab.text() == "60%"
shot(w, "04b_strength.png")
w.strength.setValue(0)
# 후보정 시안 화면
w.work.set_value("post"); w._work_changed()
w.post["vocals"].set_value(["mute"]); w.post["denoise"].set_value(["mid"]); w.post["clean"].set_value(["click"])
w.stages["edit"].set_on(False); w._post_sum(); w._sync()
po = w.post_plan()
assert po.vocals == "mute" and po.denoise == "mid" and po.clean == ["click"] and not po.edit_on() and not po.fix_on()
assert w.stages["edit"].summary == "꺼짐" and w.stages["inst"].summary == "보컬 뺌" and not w.eng_row2.isEnabled()
assert "파일 2개 → 결과 2개 · 악기 → 음향" in w.sum.text(), w.sum.text()
QApplication.processEvents(); QApplication.processEvents(); shot(w, "post_all.png")
w.stages["edit"].set_on(True)
w.work.set_value("new"); w._work_changed()
# 스타일 고르기 = 하나만 · 지시문 자동 채움
w.style.btns["jazz"].click()
assert w.style_key() == "jazz" and w.cap.toPlainText().startswith("Jazz trio") and w.plan().caption == ""
# 지시문 직접 고침 → 표시 · 되돌리기
w.cap.setPlainText(w.cap.toPlainText() + " Slow sea turtle gliding over coral.")
pump(0.1)
assert w.plan().caption.endswith("coral.") and "직접 고침" in w.cap_note.text() and w.b_reset.isEnabled()
w.bpm_mode.set_value("set")
w.feel.set_value("on")
w._sync()
assert w.bpm.isEnabled() and w.plan().src_feel and "원곡 느낌" in w.ref_lab.text()
shot(w, "05_work_custom.png")
w.b_reset.click()
assert w.plan().caption == "" and w.cap.toPlainText().startswith("Jazz trio")
w.bpm_mode.set_value("src")
w.feel.set_value("off")
w.stages["analyze"].set_on(False)                                         # 분석 끔 = 원곡 안 씀
w._sync()
assert not w.len_mode.isEnabled() and w.plan().length_mode == "set"
w.stages["analyze"].set_on(True)
w.style.btns["chill"].click()
w._sync()

# 실행(가짜 엔진)
os.environ["DTMC_ENGINE_URL"] = url
w.out_dir = str(tmp / "out")
w._sync()
w.start()
pump(0.3)
assert w.b_cancel.isVisible() and not w.b_start.isVisible()      # 생성 중 = [취소]만
pump(0.9)
shot(w, "06_work_running.png")
for _ in range(600):
    pump(0.05)
    if w.mode == "done":
        break
assert w.mode == "done", w.mode
from dtmc.ui_main import ResultRow  # noqa: E402

rows = w.findChildren(ResultRow)
assert len(rows) == 4, len(rows)
for r in rows:
    assert Path(r.path).exists() and Path(r.path).name.startswith("CHL_"), r.path
    assert not r.b_src.isHidden() and r.src_path          # (방금 넣은 줄은 아직 안 그려졌을 수 있어 isVisible 대신)
shot(w, "07_work_done.png")
assert w.b_cancel.isVisible() and w.b_cancel.text() == "폴더 열기" and w.b_start.text() == "돌아가기"
w.back_to_setup()
assert w.mode == "setup" and w.b_start.text() == "AUTO"
assert w.b_start.isVisible() and not w.b_cancel.isVisible()      # 설정 화면 = [AUTO]만
assert w.b_close.isVisible()                                      # 오른쪽 위 [✕]

# 길이 맞추기 시험 버튼: 원곡 있을 때만 · '길이 = 직접'일 때만 시작
assert w.b_fit.isEnabled()
seen = []
ui_style.notice = lambda _w, m, *a, **k: seen.append(m)
w.len_mode.set_value("src")
w.start_fit()
assert w.mode == "setup" and seen and "직접" in seen[-1], seen
ui_style.notice = lambda *a, **k: None

# 스타일 샘플(가짜 엔진) → 12줄
w._set_refs([str(a2), str(a1)])
assert "Night Drive" in w.ref_lab.text() and "외 1개" in w.ref_lab.text()
w.start_compare()
for _ in range(1500):
    pump(0.05)
    if w.mode == "done":
        break
assert w.mode == "done"
from PySide6.QtCore import QEvent  # noqa: E402

app.sendPostedEvents(None, QEvent.DeferredDelete)      # 지난 결과 줄(deleteLater) 정리
rows = w.findChildren(ResultRow)
names = sorted(Path(r.path).name for r in rows)
assert len(names) == 12 and len({n[:3] for n in names}) == 12 and all("_봄날의_샘플_" in n and n.endswith("_1m.mp3") for n in names), names
shot(w, "08_samples_done.png")
w.back_to_setup()
print("OK test_ui_smoke")
os._exit(0)
