"""v0.1.6 화면 시안(상의용, 실제 코드에 반영 안 함): 원곡 반영 슬라이더 · [비교 시험] · 결과 줄 [원곡] 버튼.
실제 WorkWindow 를 그대로 띄우고 생성 단계만 바꿔 그림 → ui_shots/proto_*.png"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["DTMC_USER_DIR"] = tempfile.mkdtemp(prefix="dtmc_user_")
os.environ["DTMC_ENGINE_DIR"] = tempfile.mkdtemp(prefix="dtmc_engine_")
os.environ["DTMC_MULTI"] = "1"
SHOTS = ROOT / "ui_shots"
SHOTS.mkdir(exist_ok=True)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QSlider  # noqa: E402

app = QApplication(sys.argv)
app.setFont(QFont("Noto Sans KR", 9))
import main as M  # noqa: E402
from dtmc import ff, hw, log, prompt, ui_main  # noqa: E402
from dtmc.ui_widgets import SUB_TXT, WARN, Multi, Seg, labeled, small_button  # noqa: E402

log.setup()
M.dark(app)

SLIDER_CSS = """
QSlider::groove:horizontal{height:6px; background:#3a3a3a; border-radius:3px;}
QSlider::sub-page:horizontal{background:#3d6fb6; border-radius:3px;}
QSlider::handle:horizontal{background:#e6e6e6; width:14px; margin:-5px 0; border-radius:7px;}
QSlider::handle:horizontal:disabled{background:#777;}
"""


def gen_body(self):
    W = ui_main.WorkWindow
    self.instr = Multi([(k, ko) for k, ko, _ in prompt.INSTRUMENTS], ["piano"], limit=prompt.MAX_INSTR, cols=4)
    self.instr.changed.connect(lambda _v: self._sync())
    self.instr_note = self._hint("최대 3개까지 · 고른 악기만 쓰도록 엔진에 강하게 지시합니다.")
    self.instr.refused.connect(self._instr_refused)
    self.len_mode = Seg([("src", "원곡 길이"), ("set", "직접")], "src")
    self.len_mode.changed.connect(lambda _k: self._sync())
    from PySide6.QtWidgets import QSpinBox
    self.secs = QSpinBox()
    self.secs.setRange(prompt.LEN_MIN, prompt.LEN_MAX)
    self.secs.setSuffix(" 초")
    self.secs.setValue(60)
    self.secs.setFixedWidth(96)
    self.secs_lab = QLabel("")
    self.secs_lab.setFixedWidth(40)
    self.secs_lab.setStyleSheet(f"color:{SUB_TXT};")
    lrow = QHBoxLayout()
    lrow.setSpacing(6)
    lrow.addWidget(self.len_mode, 1)
    lrow.addWidget(self.secs)
    lrow.addWidget(self.secs_lab)
    # 원곡 반영 슬라이더
    self.strength = Seg(prompt.STRENGTHS, "low")      # 기존 코드 호환용(화면엔 안 보임)
    self.slider = QSlider(Qt.Horizontal)
    self.slider.setRange(0, 20)                        # 5% 단위
    self.slider.setValue(14)
    self.slider.setStyleSheet(SLIDER_CSS)
    self.slider.valueChanged.connect(lambda _v: self._sync())
    self.sl_val = QLabel("")
    self.sl_val.setFixedWidth(44)
    self.sl_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    lo, hi = QLabel("0%"), QLabel("100%")
    for x in (lo, hi):
        x.setStyleSheet(f"color:{SUB_TXT}; font-size:11px;")
    srow = QHBoxLayout()
    srow.setSpacing(8)
    srow.addWidget(lo)
    srow.addWidget(self.slider, 1)
    srow.addWidget(hi)
    srow.addWidget(self.sl_val)
    self.str_hint = self._hint("")
    self.cand = Seg([(str(i), f"{i}곡") for i in (1, 2, 3, 4)], str(hw.profile().candidates))
    self.cand.changed.connect(lambda _k: self._sync())
    self.b_test = small_button("악기 시험", 88, 28, "8개 악기를 20초씩")
    self.b_cmp = small_button("비교 시험", 88, 28, "반영 30·50·70·90% 를 같은 씨앗으로 30초씩")
    trow = QHBoxLayout()
    trow.setSpacing(8)
    trow.addWidget(self.b_test)
    trow.addWidget(self.b_cmp)
    trow.addWidget(self._hint("비교 시험 = 첫 원곡 앞 30초를 반영 30·50·70·90%로 하나씩(같은 씨앗)."), 1)
    return self._body([labeled("악기", self.instr), labeled("", self.instr_note), labeled("길이", lrow),
                       labeled("원곡 반영", srow), labeled("", self.str_hint),
                       labeled("후보", self.cand), trow])


orig_sync = ui_main.WorkWindow._sync


def sync(self):
    orig_sync(self)
    if not hasattr(self, "slider"):
        return
    v = self.slider.value() * 5
    self.sl_val.setText(f"{v}%")
    self.sl_val.setStyleSheet(f"color:{WARN if v > 80 else '#e6e6e6'}; font-weight:bold;")
    if v == 0:
        t, c = "원곡 소리는 쓰지 않고 템포·조성·분위기만 참고해 새로 만듭니다(저작권 가장 안전).", SUB_TXT
    elif v <= 40:
        t, c = "원곡 흐름을 조금 따르고 나머지는 자유롭게 만듭니다.", SUB_TXT
    elif v <= 80:
        t, c = "원곡의 화음 진행·구조를 대부분 따라가며 악기만 바꿉니다. 템포·조성은 원곡에서 그대로.", SUB_TXT
    else:
        t, c = "원곡과 매우 닮습니다. 공개·판매용이면 원곡과 너무 닮지 않았는지 꼭 확인하세요.", WARN
    self.str_hint.setText(t)
    self.str_hint.setStyleSheet(f"color:{c}; font-size:11px;")
    an = self.stages["analyze"]
    self.bpm_mode.setEnabled(v == 0 or not an.on)            # ⑦ 반영>0 이면 템포는 원곡 그대로
    self.bpm.setEnabled(v == 0 and (self.bpm_mode.value() == "set" or not an.on))
    p = self.plan()
    len_txt = "원곡 길이" if p.length_mode == "src" else ui_main.mmss(p.seconds)
    self.stages["generate"].set_summary(f"{prompt.instr_label(p.instruments)} · {len_txt} · 반영 {v}% · 후보 {p.candidates}")
    if v > 0:
        an.set_summary(("분위기 자동" if not p.moods else " · ".join(prompt.MOOD[m][0] for m in p.moods))
                       + " · 템포·조성 = 원곡 그대로")


ui_main.WorkWindow._generate_body = gen_body
ui_main.WorkWindow._sync = sync

orig_row = ui_main.ResultRow.__init__


def row_init(self, info, parent=None):
    orig_row(self, info, parent)
    b = small_button("원곡", 48, 26, "같은 위치의 원곡 듣기(번갈아 비교)")
    self.layout().insertWidget(1, b)


ui_main.ResultRow.__init__ = row_init


def pump(sec):
    t = time.monotonic() + sec
    while time.monotonic() < t:
        app.processEvents()
        time.sleep(0.02)


def shot(w, name):
    pump(0.3)
    w.grab().save(str(SHOTS / name))


tmp = Path(tempfile.mkdtemp(prefix="dtmc_ui_"))
a1 = ff.make_test_tone(tmp / "Tsuki ni Kawatte.wav", 40, 110)
a = M.App(app)
a.open_work([str(a1)])
w = a.work
for _ in range(200):
    pump(0.05)
    if all(s.analysis is not None for s in w.sources):
        break
w.instr.set_value(["piano"])
w.cand.set_value("2")
def only(k):
    for kk, sr in w.stages.items():
        sr.set_open(kk == k)


only("generate")
w._sync()
shot(w, "proto_1_slider70.png")
w.slider.setValue(18)
shot(w, "proto_2_slider90.png")
w.slider.setValue(0)
only("analyze")
shot(w, "proto_3_slider0_tempo.png")
w.slider.setValue(14)
shot(w, "proto_4_slider70_tempo_locked.png")

# 비교 시험 결과 화면(가짜 결과 줄)
w.mode = "done"
w.rw.setVisible(False)
w.run_w.setVisible(True)
w.step_bar.set_steps(["engine", "generate"])
w.step_bar.finish("4곡")
for pct in (30, 50, 70, 90):
    w._on_item({"path": str(tmp / f"Tsuki ni Kawatte_피아노_반영{pct}.mp3"), "source": "Tsuki ni Kawatte",
                "instr": f"피아노 · 반영 {pct}% · 씨앗 1234", "seconds": 30})
w.run_lab.halt("비교 시험 완료 · 4곡 · ▶ 결과 / [원곡] 번갈아 들어 보고 마음에 드는 % 를 고르세요")
w.b_cancel.setText("폴더 열기")
w.b_start.setText("돌아가기")
w._sync()
shot(w, "proto_5_compare_done.png")
print("OK proto")
os._exit(0)
