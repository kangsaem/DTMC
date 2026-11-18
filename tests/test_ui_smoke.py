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

a.open_work([str(a1), str(a2)])
w = a.work
for _ in range(200):
    pump(0.05)
    if all(s.analysis is not None for s in w.sources):
        break
assert all(s.analysis is not None for s in w.sources), [s.error for s in w.sources]
assert 90 < w.sources[0].analysis.bpm < 102, w.sources[0].analysis.bpm
w.instr.set_value(["piano", "strings"])
w.cand.set_value("2")
w._sync()
shot(w, "03_work_generate.png")
w._open_stage("analyze")
shot(w, "04_work_analyze.png")
w._open_stage("output")
shot(w, "05_work_output.png")
w._open_stage("generate")

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
    assert Path(r.path).exists()
shot(w, "07_work_done.png")
assert w.b_cancel.isVisible() and w.b_cancel.text() == "폴더 열기" and w.b_start.text() == "돌아가기"
w.back_to_setup()
assert w.mode == "setup" and w.b_start.text() == "AUTO"
assert w.b_start.isVisible() and not w.b_cancel.isVisible()      # 설정 화면 = [AUTO]만
assert w.b_close.isVisible()                                      # 오른쪽 위 [✕]
print("OK test_ui_smoke")
os._exit(0)
