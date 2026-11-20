"""작업 창(DTME 자동 창 틀): 왼쪽 = 원곡 목록, 오른쪽 = 단계 줄 3개(분석 · 생성 · 출력), 아래 = [취소][AUTO].
[AUTO] = 작업 화면(단계 막대 + 결과 줄, 미리듣기) → 끝나면 [폴더 열기][확인] → [돌아가기] = 설정 화면.
처음 [AUTO] 때 엔진이 없으면 설치 창(ui_engine)부터."""
from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (QSlider, QDoubleSpinBox, QAbstractItemView, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPlainTextEdit,
                               QListWidgetItem, QMenu, QScrollArea, QSizePolicy, QSpinBox, QStyle,
                               QStackedWidget, QStyledItemDelegate, QVBoxLayout, QWidget)

from . import post as PO
from . import __version__, engine as EG, ff, hw, jobs, log, paths, prompt, settings, taskbar, ui_style
from .ui_widgets import (CHK_BG, SUB, SUB_TXT, WARN, Busy, DragMove, Grip, MagnetSlider, Multi, Seg, StageRow, StepBar,
                         WaveView,
                         BTN_H, TEST_W, grid4, labeled, main_button, no_hfw, open_folder, small_button)

L = log.log
EMPTY_HINT = ("원곡(음원 파일) · 폴더를 여기에 끌어다 놓기 — 없어도 만들 수 있음", "우클릭 = 파일 추가 · 폴더 추가")
LIST_H = 140                   # 시안 A: 위 띠 원곡 목록 높이(3줄)
ENGINES = [("1.7B", "1.7B"), ("4B", "4B"), ("XL", "XL")]
STRENGTH_STEP = 5              # 원곡 반영 슬라이더 단위(%) — 사용자 11/19: 5 단위(55~65% 가 좋을 것 같다고)
COL_W = 470                    # 설정 한 줄 폭(DTME v0.17.2 자동 창과 같게 — 창 1400×720, 두 줄 다 펼침)
AUDIO_FILTER = "음원 (" + " ".join(f"*{e} *{e.upper()}" for e in sorted(ff.AUDIO_EXT)) + ")"
ROLE = Qt.UserRole + 1



def mmss(sec: float) -> str:
    sec = int(round(sec or 0))
    return f"{sec // 60}:{sec % 60:02d}"


def collect_audio(items) -> list[str]:
    out = []
    for p in items:
        p = Path(p)
        if p.is_dir():
            out += sorted(str(f) for f in p.iterdir() if f.is_file() and f.suffix.lower() in ff.AUDIO_EXT)
        elif p.is_file() and p.suffix.lower() in ff.AUDIO_EXT:
            out.append(str(p))
    return out


# ------------------------------------------------------------ 원곡 목록
class RowDelegate(QStyledItemDelegate):
    """[✓] 이름 ··· 길이 / 아래 줄 = 분석 결과(또는 분석 중·오류). 켠 줄 = 파란 기운(#2b3a4a)."""

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), 46)

    def paint(self, p, option, index):
        s: jobs.Source = index.data(ROLE)
        if s is None:
            return
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(option.rect).adjusted(1, 1, -1, -1)
        sel = bool(option.state & QStyle.State_Selected)
        p.setPen(QPen(QColor("#4a8fe0"), 1) if sel else Qt.NoPen)
        p.setBrush(QColor("#2b3a4a" if s.checked else "#242424"))
        p.drawRoundedRect(r, 3, 3)
        cb = QRectF(r.left() + 10, r.top() + 15, 16, 16)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(CHK_BG))
        p.drawRoundedRect(cb, 2, 2)
        if s.checked:
            p.setPen(QPen(QColor("#ffffff"), 1.8))
            p.drawLine(cb.left() + 4, cb.center().y(), cb.left() + 7, cb.bottom() - 4)
            p.drawLine(cb.left() + 7, cb.bottom() - 4, cb.right() - 4, cb.top() + 4)
        f = QFont(option.font)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#e6e6e6" if s.checked else "#8a8a8a"))
        right = 64
        nr = QRectF(r.left() + 36, r.top() + 4, r.width() - 36 - right, 20)
        p.drawText(nr, Qt.AlignLeft | Qt.AlignVCenter, p.fontMetrics().elidedText(s.name, Qt.ElideMiddle, int(nr.width())))
        f.setBold(False)
        p.setFont(f)
        p.setPen(QColor(SUB_TXT))
        p.drawText(QRectF(r.right() - right, r.top() + 4, right - 10, 20), Qt.AlignRight | Qt.AlignVCenter,
                   mmss(s.duration) if s.duration else "")
        if s.error:
            sub, col = "읽을 수 없는 파일 — " + s.error, "#d9604f"
        elif s.analysis is None:
            sub, col = "분석 중…", SUB_TXT
        else:
            a = s.analysis
            moods = " · ".join(prompt.MOOD[m][0] for m in a.moods if m in prompt.MOOD)
            sub, col = f"{a.bpm:g} BPM · {a.key_label} · {moods}", SUB
        sr = QRectF(r.left() + 36, r.top() + 23, r.width() - 46, 18)
        p.setPen(QColor(col))
        p.drawText(sr, Qt.AlignLeft | Qt.AlignVCenter, p.fontMetrics().elidedText(sub, Qt.ElideRight, int(sr.width())))
        p.restore()


class SourceList(QListWidget):
    toggled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setItemDelegate(RowDelegate(self))
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setFocusPolicy(Qt.NoFocus)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSpacing(2)
        self.setStyleSheet("QListWidget{background:#1e1e1e; border:1px solid #333; border-radius:3px; padding:4px;}"
                           "QListWidget::item{background:transparent;}")

    def mousePressEvent(self, e):
        it = self.itemAt(e.position().toPoint())
        if it is not None and e.button() == Qt.LeftButton:
            r = self.visualItemRect(it)
            if e.position().x() - r.left() < 34:
                s = it.data(ROLE)
                s.checked = not s.checked
                self.viewport().update()
                self.toggled.emit()
                return
        super().mousePressEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.count() == 0:
            p = QPainter(self.viewport())
            p.setPen(QColor("#7a7a7a"))
            r = self.viewport().rect()
            p.drawText(r.adjusted(0, -12, 0, -12), Qt.AlignCenter, EMPTY_HINT[0])
            p.setPen(QColor("#5f5f5f"))
            p.drawText(r.adjusted(0, 14, 0, 14), Qt.AlignCenter, EMPTY_HINT[1])
            p.end()


# ------------------------------------------------------------ 결과 줄(미리듣기)
class ResultRow(QWidget):
    """[▶][원곡] 이름 / 아래 작은 글씨 = 원곡 · 악기 · 반영 · 길이 … [폴더]. [원곡] = 같은 위치의 원곡(번갈아 듣기)."""
    play = Signal(object)
    play_src = Signal(object)
    redo = Signal(object)

    def __init__(self, info: dict, parent=None):
        super().__init__(parent)
        self.info = info
        self.path = info["path"]
        self.setObjectName("ResultRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("#ResultRow{background:#2a2a2a; border:1px solid #3a3a3a; border-radius:3px;}"
                           "QLabel{background:transparent;}")
        self.setFixedHeight(44)
        self.b_play = small_button("▶", 36, BTN_H, "미리듣기")
        self.b_play.clicked.connect(lambda: self.play.emit(self))
        self.src_path = info.get("src_path") or ""
        self.b_src = small_button("원곡", 56, BTN_H, "원곡을 처음부터 듣기(분위기 비교)")
        self.b_src.clicked.connect(lambda: self.play_src.emit(self))
        self.b_src.setVisible(bool(self.src_path))
        parts = ([info["label"]] if info.get("label") else []) + \
            [info.get("source", ""), info.get("instr", ""), mmss(info.get("seconds", 0))] + \
            ([f"씨앗 {info['seed']}"] if info.get("seed") else [])
        self._texts = (Path(self.path).name, " · ".join(x for x in parts if x))
        name = QLabel(self._texts[0])
        name.setStyleSheet("color:#e6e6e6;")
        name.setToolTip(self.path)
        self.name = name
        sub = QLabel(self._texts[1])
        sub.setStyleSheet(f"color:{SUB_TXT}; font-size:11px;")
        sub.setToolTip(self._texts[1])
        self.sub = sub
        for x in (name, sub):                     # 긴 이름은 줄임(…) — 줄이 칸보다 넓어져 가로 스크롤이 생기지 않게
            x.setMinimumWidth(1)
            x.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(name)
        col.addWidget(sub)
        self.b_redo = small_button("다시 입히기", 92, BTN_H,
                                   "이 곡을 원본으로 소리를 새로 입혀 4가지로(시험): shift3 · XL + 녹음 표현 · XL 70% · "
                                   "XL + 음색 참고('음색 참고' 칸이 있을 때). 구성은 따라가고 음색을 실제 연주 쪽으로")
        self.b_redo.clicked.connect(lambda: self.redo.emit(self))
        b_dir = small_button("폴더", 56, BTN_H, "저장한 곳 열기")
        b_dir.clicked.connect(lambda: open_folder(self.path, select=True))
        h = QHBoxLayout(self)
        h.setContentsMargins(8, 4, 8, 4)
        h.setSpacing(8)
        h.addWidget(self.b_play)
        h.addWidget(self.b_src)
        h.addLayout(col, 1)
        h.addWidget(self.b_redo)
        h.addWidget(b_dir)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        for lab, t in ((self.name, self._texts[0]), (self.sub, self._texts[1])):
            mode = Qt.ElideMiddle if lab is self.name else Qt.ElideRight
            lab.setText(lab.fontMetrics().elidedText(t, mode, max(10, lab.width())))

    def set_playing(self, which: str = ""):
        """which = 'res'(결과) · 'src'(원곡) · ''(멈춤)."""
        self.b_play.setText("■" if which == "res" else "▶")
        ui_style.tone(self.b_play, "blue" if which == "res" else "")
        self.b_src.setText("■ 원곡" if which == "src" else "원곡")
        ui_style.tone(self.b_src, "blue" if which == "src" else "")


class Player:
    """미리듣기(QtMultimedia). 못 쓰면 None 처럼 동작(▶ = 파일 열기). ▶ = 결과 · [원곡] = 원곡(둘 다 처음부터)."""

    def __init__(self, parent):
        self.parent = parent
        self.cur: ResultRow | None = None
        self.which = ""                         # res | src
        self.mp = None
        self._tried = False

    def _ensure(self):
        """처음 ▶ 를 누를 때 만듦(오디오 장치 준비가 시작을 늦추거나 막지 않게)."""
        if self._tried:
            return self.mp
        self._tried = True
        if os.environ.get("DTMC_NO_AUDIO") == "1":
            return None
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            self.mp = QMediaPlayer(self.parent)
            self.ao = QAudioOutput(self.parent)
            self.ao.setVolume(0.9)
            self.mp.setAudioOutput(self.ao)
            self.mp.playbackStateChanged.connect(self._state)
        except Exception as e:  # noqa: BLE001
            L.warning("미리듣기 못 씀: %s", e)
            self.mp = None
        return self.mp

    def toggle(self, row: ResultRow):
        self._toggle(row, "res", row.path)

    def toggle_src(self, row: ResultRow):
        if row.src_path:
            self._toggle(row, "src", row.src_path)

    def _toggle(self, row, which, path):
        if self._ensure() is None:
            open_folder(path)
            return
        same = self.cur is row and self.which == which
        self.stop()
        if same:
            return
        self.cur, self.which = row, which
        self.mp.setSource(QUrl.fromLocalFile(path))
        self.mp.play()
        row.set_playing(which)

    def stop(self):
        if self.mp is not None:
            self.mp.stop()
        if self.cur is not None:
            try:
                self.cur.set_playing("")
            except RuntimeError:
                pass
        self.cur, self.which = None, ""

    def _state(self, st):
        from PySide6.QtMultimedia import QMediaPlayer
        if st == QMediaPlayer.StoppedState and self.cur is not None:
            try:
                self.cur.set_playing("")
            except RuntimeError:
                pass
            self.cur, self.which = None, ""


class Panel(QWidget):
    """오른쪽 칸(폭 고정): 높이-폭 계산을 안 함 → 단계 줄을 펼쳐도 창 최소 높이가 커지지 않음."""

    def hasHeightForWidth(self):
        return False


# ------------------------------------------------------------ 작업 창
class WorkWindow(DragMove, QWidget):

    def __init__(self, eng: EG.Engine):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint | Qt.WindowMinimizeButtonHint)
        self.eng = eng
        self.setObjectName("Work")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("#Work{background:#262626; border:1px solid #555; border-radius:6px;}")
        self.setWindowTitle(f"DTMC {__version__}")
        self.setAcceptDrops(True)
        self.sources: list[jobs.Source] = []
        self.ajobs: list[jobs.AnalyzeJob] = []
        self.job: jobs.MakeJob | None = None
        self.mode = "setup"                     # setup | run | done
        self._compare = False
        self._fit = False
        self.cfg = settings.get("work", {}) or {}
        self.player = Player(self)
        self._step_key, self._step_t0 = "", time.monotonic()
        self._ticker = QTimer(self)
        self._ticker.setInterval(1000)
        self._ticker.timeout.connect(self._tick)
        self._ticker.start()
        self._build()
        self._load_cfg()
        self._load_post()
        self._sync()

    # ================================================================ 화면
    def _build(self):
        self.list = SourceList()
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(lambda pos: self._menu(self.list.viewport().mapToGlobal(pos)))
        self.list.itemSelectionChanged.connect(self._sel_changed)
        self.list.toggled.connect(self._sync)
        QShortcut(QKeySequence(Qt.Key_Delete), self, activated=self.remove_selected)
        QShortcut(QKeySequence.SelectAll, self, activated=self.list.selectAll)
        self.list.setFixedHeight(LIST_H)          # 시안 A: 원곡 목록 = 위 띠(3줄 · 스크롤)
        # ---- 위 띠 오른쪽: 실행 모드 · 엔진 · 요약
        pr = hw.profile()
        self.mode_lab = no_hfw(QLabel(pr.label), 1)
        self.mode_lab.setMinimumWidth(1)
        self.mode_lab.setStyleSheet(f"color:{SUB_TXT};")
        self.eng_lab = no_hfw(QLabel(""), 1)
        self.eng_lab.setMinimumWidth(1)
        self.eng_lab.setStyleSheet(f"color:{SUB_TXT};")
        self.warn_lab = no_hfw(QLabel(pr.warn), 2)
        self.warn_lab.setStyleSheet(f"color:{WARN};")
        self.warn_lab.setVisible(bool(pr.warn))
        self.sum = no_hfw(QLabel(""), 2)
        self.sum.setStyleSheet(f"color:{SUB};")
        self.wave = WaveView()                                     # 후보정: 고른 파일 파형 · 끌어서 '부분 다시' 구간
        self.wave.region.connect(self._wave_region)
        self.wave.setVisible(False)
        self.stages = {
            "analyze": StageRow("analyze", self._analyze_body()),
            "generate": StageRow("generate", self._generate_body(), fixed=True),
            "output": StageRow("output", self._output_body(), fixed=True),
            **{k: StageRow(k, b) for k, b in self._post_sections().items()},
        }
        self.stages["analyze"].toggled.connect(lambda _k, _v: self._sync())
        for k in ("edit", "sound", "inst", "fix"):                 # 후보정 칸 체크 = 칸 전체 켬/끔(설정은 기억)
            self.stages[k].toggled.connect(lambda _k, _v: self._post_sum())
        info = QVBoxLayout()
        info.setSpacing(4)
        info.setContentsMargins(0, 0, 0, 0)
        info.addWidget(grid4([(self.work, 2), (None, 2)]))  # 맨 위 = 작업 고르기(새로 만들기 | 후보정, 시안 I · 주황)
        info.addWidget(self.mode_lab)
        info.addWidget(self.eng_lab)
        info.addWidget(self.warn_lab)
        info.addWidget(self.sum)
        info.addStretch(1)
        band = QHBoxLayout()
        band.setSpacing(16)
        band.addWidget(self.list, 6)
        band.addLayout(info, 5)
        right = QVBoxLayout()
        right.setSpacing(8)
        right.setContentsMargins(0, 0, 0, 0)
        two = QHBoxLayout()                       # DTME v0.17.2 B안과 같은 틀: 두 줄, 늘 다 펼침
        two.setSpacing(10)
        for keys in (("analyze", "edit", "sound", "output"), ("generate", "inst", "fix")):
            col = QVBoxLayout()
            col.setSpacing(8)
            for k in keys:
                col.addWidget(self.stages[k])
            col.addStretch(1)
            two.addLayout(col, 1)
        right.addLayout(two)
        self.rw = Panel()
        self.rw.setLayout(right)
        self.rw.setFixedWidth(COL_W * 2 + 10)
        for k in ("edit", "sound", "inst", "fix"):                # 처음 = 새로 만들기(후보정 칸 숨김)
            self.stages[k].setVisible(False)
        # ---- 작업 중: 결과 줄 · 글자 · 막대
        self.res_box = QVBoxLayout()
        self.res_box.setContentsMargins(0, 0, 0, 0)
        self.res_box.setSpacing(5)
        self.res_box.addStretch(1)
        res_w = QWidget()
        res_w.setLayout(self.res_box)
        self.res_scroll = QScrollArea()
        self.res_scroll.setWidgetResizable(True)
        self.res_scroll.setWidget(res_w)
        self.res_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.res_scroll.setStyleSheet("QScrollArea{border:none; background:transparent;} QWidget{background:transparent;}")
        self.run_lab = Busy()
        no_hfw(self.run_lab, 2)
        self.run_lab.setStyleSheet(f"color:{SUB};")
        self.step_bar = StepBar()
        runl = QVBoxLayout()
        runl.setContentsMargins(0, 0, 0, 0)
        runl.setSpacing(10)
        runl.addWidget(self.res_scroll, 1)
        runl.addWidget(self.run_lab)
        runl.addWidget(self.step_bar)
        self.run_w = Panel()
        self.run_w.setLayout(runl)
        self.run_w.setFixedWidth(COL_W * 2 + 10)
        self.run_w.setVisible(False)
        mid = QVBoxLayout()
        mid.setSpacing(8)
        mid.addLayout(band)
        self.wave.setFixedHeight(84)                              # [시안] 파형 = 창 폭 전체 띠
        mid.addWidget(self.wave)
        mid.addWidget(self.rw, 1)
        mid.addWidget(self.run_w, 1)
        # ---- 아래
        self.ver_lab = QLabel(f"DTMC {__version__}")
        self.ver_lab.setStyleSheet("color:#6f6f6f; font-size:11px;")
        self.state_lab = no_hfw(QLabel(""), 2)              # 긴 글이 창 최소 폭을 늘리지 않게(윈도우 글꼴이 더 넓음)
        self.state_lab.setMinimumWidth(1)
        self.b_cancel = main_button("취소")
        self.b_cancel.clicked.connect(self.cancel)
        self.b_start = main_button("AUTO", "red")
        self.b_start.clicked.connect(self.start)
        bot = QHBoxLayout()
        bot.setSpacing(8)
        bot.addWidget(self.ver_lab)
        bot.addSpacing(12)
        tw = QWidget()
        tw.setLayout(self.trow)
        self.trow.setContentsMargins(0, 0, 0, 0)
        self.test_w = tw
        bot.addWidget(tw)
        bot.addSpacing(8)
        bot.addWidget(self.state_lab, 1)
        bot.addWidget(self.b_cancel)
        bot.addWidget(self.b_start)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(Grip(), 1)
        top.addWidget(QPushButton_min(self))
        self.b_close = QPushButton_close(self)
        top.addWidget(self.b_close)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 8, 16, 14)
        lay.setSpacing(8)
        lay.addLayout(top)
        lay.addLayout(mid, 1)
        lay.addLayout(bot)
        self.resize(COL_W * 2 + 10 + 32, 720)

    def _body(self, rows) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 4, 12, 12)
        v.setSpacing(9)
        for r in rows:
            if isinstance(r, QWidget):
                v.addWidget(r)
            else:
                v.addLayout(r)
        return w

    def _hint(self, text="") -> QLabel:
        h = QLabel(text)
        h.setStyleSheet(f"color:{SUB_TXT}; font-size:11px;")
        return no_hfw(h, 2)

    def _analyze_body(self) -> QWidget:
        """원곡에서 가져올 것: 조성 · 템포(자동일 때) · 느낌(켰을 때만). 단계를 끄면 원곡을 쓰지 않음."""
        self.a_info = no_hfw(QLabel(""), 3)
        self.a_info.setStyleSheet(f"color:{SUB};")
        self.bpm_mode = Seg([("src", "자동"), ("set", "직접")], "src")
        self.bpm_mode.setToolTip("자동 = 원곡 템포가 스타일에 어울리면 그 템포, 아니면 엔진이 정함 · 직접 = 정한 숫자대로")
        self.bpm_mode.changed.connect(lambda _k: self._sync())
        self.bpm = QSpinBox()
        self.bpm.setRange(40, 200)
        self.bpm.setSuffix(" BPM")
        self.bpm.setValue(90)
        self.bpm.valueChanged.connect(lambda _v: self._sync())
        brow = grid4([(self.bpm_mode, 2), (self.bpm, 1), (None, 1)])
        self.feel = Seg([("off", "안 씀"), ("on", "원곡 느낌 참고")], "off")
        self.feel.setToolTip("원곡 소리에서 음색·분위기만 참고합니다(멜로디는 따라가지 않음). "
                             "켜면 '음색 참고' 칸 대신 원곡을 씁니다. 공개·판매용이면 원곡과 너무 닮지 않았는지 확인하세요.")
        self.feel.changed.connect(lambda _k: self._sync())
        return self._body([self.a_info, labeled("템포", brow), labeled("원곡 소리", grid4([(self.feel, 2), (None, 2)])),
                           labeled("원곡 반영", self._strength_row()), labeled("음색 참고", self._ref_row())])

    def _strength_row(self):
        """원곡 반영 정도: 0 = 안 씀(원곡 정보만) · 5~100%(5% 단위, v0.1.19) = 원곡 구성을 따라가는 커버(엔진 cover strength).
        값 = 슬라이더 칸 × STRENGTH_STEP."""
        self.strength = QSlider(Qt.Horizontal)
        self.strength.setRange(0, 100 // STRENGTH_STEP)
        self.strength.setPageStep(1)
        self.strength.setTickPosition(QSlider.TicksBelow)
        self.strength.setTickInterval(10 // STRENGTH_STEP)            # 눈금 = 10% 마다
        self.strength.setToolTip("0 = 안 씀(지금처럼 원곡의 조성·템포·길이만) · 높을수록 원곡의 멜로디·코드·구성을 더 따라감\n"
                                 "켜면 길이 = 원곡 길이 · 작곡 담당은 쓰지 않음(엔진이 끔) · 70% 이상은 원곡과 많이 닮을 수 있음")
        self.strength_lab = QLabel("")
        self.strength_lab.setStyleSheet(f"color:{SUB};")
        self.strength.valueChanged.connect(lambda _v: self._sync())
        self.strength_lab.setText("안 씀")
        return grid4([(self.strength, 3), (self.strength_lab, 1)])

    def _ref_row(self):
        self.refs: list[str] = []
        self.ref_lab = QLabel("")
        self.ref_lab.setStyleSheet(f"color:{SUB};")
        self.ref_lab.setMinimumWidth(1)
        self.ref_lab.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        b_ref = small_button("선택", tip=f"잘 녹음된 실제 연주 파일(최대 {prompt.MAX_REFS}개) — 음색·연주 느낌만 참고")
        b_ref.clicked.connect(self.pick_ref)
        b_refx = small_button("지움")
        b_refx.clicked.connect(lambda: self._set_refs([]))
        return grid4([(b_ref, 1), (b_refx, 1), (self.ref_lab, 2)])

    def _generate_body(self) -> QWidget:
        self.style = Multi([(k, ko) for k, ko, _c, _r in prompt.STYLES], [prompt.DEFAULT_STYLE], limit=1, cols=4)
        self.style.set_swap(True)
        self.style.changed.connect(self._style_changed)
        self.cap = QPlainTextEdit()
        self.cap.setFixedHeight(92)
        self.cap.setTabChangesFocus(True)
        self.cap.setStyleSheet("QPlainTextEdit{background:#1e1e1e; border:1px solid #3a3a3a; border-radius:3px; color:#d8d8d8;}")
        self.cap.setToolTip("엔진에 보낼 지시문(영어). 스타일을 고르면 자동으로 채워지고, 직접 고쳐 쓸 수 있습니다.")
        self.cap.textChanged.connect(lambda: self._sync())
        self.cap_note = QLabel("")
        b_reset = small_button("되돌리기", tip="고른 스타일의 지시문으로 되돌리기")
        b_reset.clicked.connect(lambda: self._set_caption(""))
        self.b_reset = b_reset
        crow = grid4([(self.cap_note, 3), (b_reset, 1)])
        self.len_mode = Seg([("src", "원곡 길이"), ("set", "직접")], "src")
        self.len_mode.changed.connect(lambda _k: self._sync())
        self.secs = QSpinBox()
        self.secs.setRange(prompt.LEN_MIN, prompt.LEN_MAX)
        self.secs.setSingleStep(10)
        self.secs.setSuffix(" 초")
        self.secs.setValue(60)
        self.secs.valueChanged.connect(lambda _v: self._sync())
        self.secs_lab = QLabel("")
        self.secs_lab.setStyleSheet(f"color:{SUB_TXT};")
        lrow = grid4([(self.len_mode, 2), (self.secs, 1), (self.secs_lab, 1)])
        self.cand = Seg([(str(i), f"{i}곡") for i in (1, 2, 3, 4)], str(hw.profile().candidates))
        self.cand.changed.connect(lambda _k: self._sync())
        # 엔진 고르기(시안): 1.7B = shift3 + 작곡 1.7B · 4B = shift3 + 작곡 4B · XL = XL + 작곡 1.7B
        big = jobs.xl_ok()
        self.engine = Seg(ENGINES, "4B" if big else "1.7B")
        self.engine.changed.connect(lambda _k: self._sync())
        self.engine.setToolTip("1.7B = 연주 shift3 + 작곡 담당 1.7B\n4B = 연주 shift3 + 작곡 담당 4B(그래픽 메모리 20GB 이상)\n"
                               "XL = 연주 XL + 작곡 담당 1.7B(그래픽 메모리 20GB 이상 · XL + 4B 는 24GB 에서 실패)\n"
                               "바꾸면 다음 생성 때 엔진을 다시 켬")
        for k in ("4B", "XL"):
            self.engine.btns[k].setEnabled(big)
            if not big:
                self.engine.btns[k].setToolTip("이 컴퓨터에서는 못 씀(그래픽 메모리 20GB 이상 필요)")
        self.cpu_lm = self.engine                              # (예전 이름)
        self.erow = grid4([(self.engine, 3), (None, 1)])
        crow2 = grid4([(self.cand, 4)])
        self.lm_note = None
        self.b_cmp = small_button("스타일 샘플", TEST_W, BTN_H,
                                  f"스타일 {len(prompt.STYLES)}가지를 {prompt.COMPARE_SECONDS}초씩 뽑아 들어 보기(같은 씨앗)")
        self.b_cmp.clicked.connect(self.start_compare)
        self.b_fit = small_button("길이 맞추기 시험", TEST_W, BTN_H,
                                  "첫 원곡을 '길이' 칸의 값(직접)에 맞춰 4가지로: 편집만 · 이음매 다시 그리기 · 커버 100% · 커버 70%(고른 스타일)")
        self.b_fit.clicked.connect(self.start_fit)
        trow = QHBoxLayout()
        trow.setSpacing(8)
        trow.addWidget(self.b_cmp)
        trow.addWidget(self.b_fit)
        self.b_voc = small_button("보컬 시험", TEST_W, BTN_H,
                                  f"애니 오프닝 {prompt.VOCAL_SECONDS}초 × 4(같은 씨앗): ① 지금 ② 가사 칸 [Instrumental] "
                                  "③ 지시문 수정 ④ 둘 다 — 노래 같은 소리가 섞이는지 비교")
        self.b_voc.clicked.connect(self.start_vocal)
        trow.addWidget(self.b_voc)
        self.trow = trow                                       # 시험 버튼 = 아래 줄(시안 A2)
        self.crow2 = crow2                                     # 후보 · 작곡 담당 = 출력 칸(시안 A2)
        new = self._body([labeled("스타일", self.style), labeled("지시문", self.cap), labeled("", crow),
                          labeled("길이", lrow), labeled("엔진", self.erow)])
        # ---- 작업 고르기(시안 I): 새로 만들기 | 후보정 — 위 정보 칸 맨 위 · 주황
        self.work = Seg([("new", "새로 만들기"), ("post", "후보정")], "new")
        self.work.setStyleSheet("QPushButton:checked{background:#c8661e; border:1px solid #f08a3c; color:white;}")
        self.work.changed.connect(lambda _k: self._work_changed())
        return new

    def _work_changed(self):
        """새로 만들기 ↔ 후보정: 쓰지 않는 칸 · 줄 · 버튼은 숨김."""
        post = self.work.value() == "post"
        if not hasattr(self, "stages"):
            return
        for k in ("analyze", "generate"):
            self.stages[k].setVisible(not post)
        for k in ("edit", "sound", "inst", "fix"):
            self.stages[k].setVisible(post)
        for wdg in (self.row_title, self.row_cand):
            wdg.setVisible(not post)
        self.wave.setVisible(post)                                # [시안] 후보정 = 창 폭 전체 파형 띠
        self.list.setFixedHeight(64 if post else LIST_H)          # 후보정은 파일 1~2개 → 목록 낮게
        for wdg in (self.mode_lab, self.eng_lab):
            wdg.setVisible(not post)
        self.warn_lab.setVisible(bool(hw.profile().warn) and not post)
        if post:
            self._wave_path = None
            self._wave_load()
        if hasattr(self, "test_w"):
            self.test_w.setVisible(not post and self.mode == "setup")
        self._sync()
        QTimer.singleShot(0, self._fit_height)

    def _fit_height(self):
        """숨긴 칸 · 줄만큼 늘었던 높이를 되돌림(레이아웃 캐시를 비우고 다시 잼 · 최소 높이보다 작게는 안 됨)."""
        for st in self.stages.values():
            st.body.updateGeometry()
            st.updateGeometry()
        for lay in (self.rw.layout(), self.layout()):
            lay.invalidate()
            lay.activate()
        self.resize(self.width(), 720)

    def _rows(self, rows) -> QWidget:
        """후보정 칸 몸통 — 줄 간격을 조금 좁게(네 칸이 한 화면에)."""
        w = self._body(rows)
        w.layout().setContentsMargins(10, 0, 12, 6)
        w.layout().setSpacing(5)
        return w

    def _post_sections(self) -> dict:
        """후보정(시안 K): '끔/그대로' 없이 — 숫자 0 = 안 함 · 슬라이더 가운데 = 그대로 · 켜는 버튼은 다시 누르면 끔.
        칸 머리 체크 = 칸 전체 켬/끔(설정은 기억)."""
        def tog(opts, one=True):
            m = Multi(opts, [], limit=1 if one else len(opts), cols=len(opts), min_one=False)
            if one:
                m.set_swap(True)
            m.changed.connect(lambda _v: self._post_sum())
            return m

        def spin(lo, hi, val, suf, step=1, zero="", pre=""):
            sp = QSpinBox()
            sp.setPrefix(pre)
            sp.setRange(lo, hi)
            sp.setValue(val)
            sp.setSuffix(suf)
            sp.setSingleStep(step)
            if zero:
                sp.setSpecialValueText(pre + zero)        # 맨 아래 값 = '그대로'(안 함)
            sp.valueChanged.connect(lambda _v: self._post_sum())
            return sp

        def slider(lo, hi, val, fmt, marks=(), tick=0):
            sl = MagnetSlider(lo, hi, val, fmt, marks, tick=tick)
            sl.changed.connect(lambda _v: self._post_sum())
            return sl

        def lab(t):
            q = QLabel(t)
            q.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            return q
        P = self.post = {}
        P["cut"] = tog([("trim", "무음 자르기"), ("bar", "마디에서 끝"), ("loop", "루프")], one=False)
        P["fade_in"] = spin(0, 10, 0, "초", zero="없음", pre="들어감 ")
        P["fade_out"] = spin(0, 20, 0, "초", zero="없음", pre="나감 ")
        P["fit_s"] = spin(5, 600, 5, "초", 5, zero="그대로", pre="길이 ")    # 맨 아래(5) = 그대로 · 10초부터 맞춤
        P["tempo"] = spin(-10, 10, 0, "%", zero="", pre="템포 ")
        P["key"] = spin(-2, 2, 0, "음", pre="조 ")
        P["fit_s"].setToolTip("영상 길이에 맞춰 마디 단위로 줄이거나 늘림(원래 소리 그대로) · 맨 아래 = 그대로")
        edit = self._rows([
            labeled("정리", grid4([(P["cut"], 3), (None, 1)])),
            labeled("페이드", grid4([(P["fade_in"], 1), (P["fade_out"], 1), (None, 2)])),
            labeled("길이·빠르기", grid4([(P["fit_s"], 1), (P["tempo"], 1), (P["key"], 1), (None, 1)]))])
        P["denoise"] = slider(0, 10, 0, lambda v: "끔" if v == 0 else f"-{v * 2}dB", tick=1)
        P["denoise"].setToolTip("잡음(쉬익 소리) 줄이는 양. 세게 하면 소리가 뭉개지거나 '찰랑'거릴 수 있음")
        dbf = lambda v: "그대로" if v == 0 else f"{v / 2:+g}dB"  # noqa: E731
        P["low"] = slider(-12, 12, 0, dbf, [(0, False), (-4, True)])
        P["low"].setToolTip("저음 = 120Hz 아래를 키우거나 줄임(0.5dB 단위). 작은 ▼ = 정리(-2dB, 웅웅거림을 덜어 냄) — "
                            "근처로 끌면 붙음 · 가운데(그대로)도 붙음")
        P["high"] = slider(-12, 12, 0, dbf, [(0, False), (-6, True)])
        P["high"].setToolTip("고음 = 8kHz 위(지글거림 · 금속성)를 키우거나 줄임. 작은 ▼ = 정리(-3dB, 3.5kHz 찌르는 소리도 "
                             "조금 함께 누름) — 근처로 끌면 붙음 · 가운데(그대로)도 붙음")
        P["clean"] = tog([("click", "튀는 소리"), ("loud", "크기 맞추기")], one=False)
        P["clean"].setToolTip("크기 맞추기 = 30Hz 아래 웅웅거림 자르기 + 압축 + 유튜브 기준(-14 LUFS) + 깨짐 막기")
        sound = self._rows([
            labeled("잡음 줄이기", P["denoise"]),
            labeled("저음", P["low"]),
            labeled("고음", P["high"]),
            labeled("정리", grid4([(P["clean"], 2), (None, 2)]))])
        STEPS = ["뺌", "-12dB", "-9dB", "-6dB", "-3dB", "그대로", "+3dB", "+6dB"]
        for k_ in ("vocals", "drums", "bass", "other"):
            P[k_] = slider(0, 7, 5, lambda v: STEPS[v])
            P[k_].setToolTip("맨 왼쪽 = 뺌 · 가운데 = 그대로 · 오른쪽 = 강조(+6dB 까지, 깨짐 막기 자동)")
        P["stems"] = tog([("on", "악기별 파일도")])
        P["stems"].setToolTip("보컬 · 드럼 · 베이스 · 나머지 4개 파일로도 저장")
        inst = self._rows([
            labeled("보컬", P["vocals"]),
            labeled("드럼", P["drums"]),
            labeled("베이스", P["bass"]),
            labeled("나머지", P["other"]),
            labeled("저장", grid4([(P["stems"], 2), (None, 2)]))])
        P["repaint"] = tog([("on", "부분 다시")])
        def dspin(val, suf):
            sp = QDoubleSpinBox()
            sp.setDecimals(2)
            sp.setRange(0, 600)
            sp.setSingleStep(0.01)
            sp.setValue(val)
            sp.setSuffix(suf)
            sp.editingFinished.connect(lambda: sp.setValue(self.wave.snap(sp.value())))   # 넣은 초 → 가까운 마디
            sp.valueChanged.connect(lambda _v: self._post_sum())
            return sp
        P["rp_a"] = dspin(30, "초부터")
        P["rp_b"] = dspin(40, "초까지")
        P["repaint"].changed.connect(lambda _v: (setattr(self.wave, "show_sel", bool(P["repaint"].value())), self.wave.update()))
        P["redo"] = tog([("on", "다시 입히기")])
        P["extend"] = tog([("on", "이어 만들기")])
        P["ext_s"] = spin(5, 120, 20, "초", 5)
        self.engine2 = Seg(ENGINES, self.engine.value())
        for k_ in ("4B", "XL"):
            self.engine2.btns[k_].setEnabled(self.engine.btns[k_].isEnabled())
        self.engine2.changed.connect(lambda k_: (self.engine.set_value(k_), self._sync()))
        b_ref2 = small_button("음색 참고", tip="다시 입히기에 함께 쓸 실제 연주 녹음(최대 3개)")
        b_ref2.clicked.connect(self.pick_ref)
        P["repaint"].setToolTip("그 구간만 새로 만들고 앞뒤와 자연스럽게 이음 — 위 파형에서 끌어서 골라도 됨")
        P["redo"].setToolTip("구성은 두고 음색만 새로(곡 전체)")
        P["extend"].setToolTip("곡 끝에 이어서 더 만듦")
        for k_ in ("rp_a", "rp_b"):
            P[k_].valueChanged.connect(lambda _v: self.wave.set_sel(P["rp_a"].value(), P["rp_b"].value()))
        self.eng_row2 = grid4([(self.engine2, 3), (None, 1)])
        fix = self._rows([
            labeled("", grid4([(P["repaint"], 1), (P["rp_a"], 1), (P["rp_b"], 1), (None, 1)])),
            labeled("", grid4([(P["redo"], 1), (b_ref2, 1), (None, 2)])),
            labeled("", grid4([(P["extend"], 1), (P["ext_s"], 1), (None, 2)])),
            labeled("엔진", self.eng_row2)])
        return {"edit": edit, "sound": sound, "inst": inst, "fix": fix}

    def _wave_region(self, a: float, b: float):
        """파형에서 끌어 고른 구간 → 부분 다시 칸(켜기까지)."""
        P = self.post
        P["rp_a"].setValue(a)
        P["rp_b"].setValue(b)
        P["repaint"].set_value(["on"])
        self._post_sum()

    def _wave_load(self):
        """원곡 목록에서 고른(없으면 첫) 파일의 파형."""
        if self.work.value() != "post":
            return
        src = self._current()
        path = src.path if src is not None else None
        if not path or getattr(self, "_wave_path", None) == path:
            return
        self._wave_path = path
        try:
            x = ff.decode_mono(path, sr=4000, max_sec=900)
            n = 1200
            m = max(1, len(x) // n)
            pk = np.abs(x[: m * n].reshape(-1, m)).max(axis=1) if len(x) >= n else np.abs(x)
            pk = pk / max(1e-6, float(pk.max()))
            an = getattr(src, "analysis", None)
            self.wave.set_audio(pk, len(x) / 4000.0, Path(path).name, getattr(an, "bpm", 0.0) or 0.0)
            self.wave.set_sel(self.post["rp_a"].value(), self.post["rp_b"].value())
        except Exception as e:  # noqa: BLE001
            L.warning("파형 읽기 실패 %s: %s", path, e)

    def _pv(self, k: str, off="off"):
        """켜는 버튼 묶음의 값(하나만 고르기) — 아무것도 안 켰으면 off."""
        v = self.post[k].value()
        return v[0] if v else off

    INST_DB = [None, -12, -9, -6, -3, 0, 3, 6]               # 악기 슬라이더 칸 → dB(None = 뺌)

    def _post_sum(self):
        """후보정 칸 머리 요약 = 그 칸에서 켠 것(칸을 끄면 '꺼짐')."""
        P = getattr(self, "post", {})
        if not P or "fix" not in getattr(self, "stages", {}):
            return
        self.wave.show_sel = bool(P["repaint"].value())
        self.wave.update()
        po = self.post_plan(all_on=True)
        parts = {
            "edit": PO.Post(cut=po.cut, fade_in=po.fade_in, fade_out=po.fade_out, fit=po.fit, fit_s=po.fit_s,
                            tempo=po.tempo, key=po.key).summary(),
            "sound": PO.Post(denoise=po.denoise, low=po.low, high=po.high, clean=po.clean).summary(),
            "inst": PO.Post(vocals=po.vocals, drums=po.drums, bass=po.bass, other=po.other, stems=po.stems).summary(),
            "fix": PO.Post(repaint=po.repaint, rp_a=po.rp_a, rp_b=po.rp_b, redo=po.redo, extend=po.extend,
                           ext_s=po.ext_s).summary()}
        if parts["fix"]:
            parts["fix"] += f" · 엔진 {self.engine.value()}"
        for k, t in parts.items():
            st = self.stages[k]
            st.set_summary("꺼짐" if not st.on else (t or "안 함"))
        self.eng_row2.setEnabled(self.stages["fix"].on and po.fix_on())   # 엔진 줄 = 고치기를 켰을 때만 진하게

    def _output_body(self) -> QWidget:
        self.fmt = Seg(ff.FORMAT_NAMES, "mp3")
        self.fmt.changed.connect(lambda _k: self._sync())
        self.out_lab = QLabel("")
        self.out_lab.setStyleSheet(f"color:{SUB};")
        self.out_lab.setMinimumWidth(1)
        self.out_lab.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.out_dir = ""
        b_ch = small_button("변경")
        b_ch.clicked.connect(self.pick_out)
        b_open = small_button("열기")
        b_open.clicked.connect(lambda: open_folder(self._out_dir()))
        orow = grid4([(self.out_lab, 2), (b_ch, 1), (b_open, 1)])
        self.title = QLineEdit()
        self.title.setMaxLength(60)
        self.title.setToolTip("파일 이름 = 스타일약어_번호_제목_길이(예: CHL_007_Dive_1m30s.mp3). 번호는 스타일마다 이어서 셈. "
                              "제목을 비우면 원곡 이름 첫 단어.")
        self.title.textChanged.connect(lambda _t: self._sync())
        def roww(lay):
            q = QWidget()
            lay.setContentsMargins(0, 0, 0, 0)
            q.setLayout(lay)
            return q
        self.row_title = roww(labeled("제목", self.title))
        self.row_cand = roww(labeled("후보", self.crow2))
        return self._body([self.row_title, labeled("형식", grid4([(self.fmt, 4)])), self.row_cand,
                           labeled("저장 폴더", orow)])

    # ================================================================ 설정 기억
    def _load_post(self):
        """후보정 설정 · 칸 체크 · 작업(새로 만들기 | 후보정) 되살리기."""
        c = settings.get("post", {}) or {}
        P = self.post
        try:
            for k in ("fade_in", "fade_out", "tempo", "key", "rp_a", "rp_b", "ext_s"):
                if isinstance(c.get(k), (int, float)):
                    P[k].setValue(int(c[k]))
            if c.get("fit") == "set":
                P["fit_s"].setValue(int(c.get("fit_s", 90)))
            if isinstance(c.get("denoise"), (int, float)):
                P["denoise"].setValue(int(c["denoise"]) // 2)
            for k in ("low", "high"):
                if isinstance(c.get(k), (int, float)):
                    P[k].setValue(int(round(c[k] * 2)))
            for k in ("vocals", "drums", "bass", "other"):
                v = c.get(k)
                if isinstance(v, (int, float)):
                    P[k].setValue(0 if v <= PO.MUTE else min(range(1, 8), key=lambda i: abs(self.INST_DB[i] - v)))
            for k in ("stems", "repaint", "redo", "extend"):
                if c.get(k) is True:
                    P[k].set_value(["on"])
            P["cut"].set_value([x for x in c.get("cut", []) if x in ("trim", "bar", "loop")])
            P["clean"].set_value([x for x in c.get("clean", []) if x in ("click", "loud")])
            for k, v in (settings.get("post_on", {}) or {}).items():
                if k in self.stages:
                    self.stages[k].set_on(bool(v))
        except Exception as e:  # noqa: BLE001
            L.warning("후보정 설정 되살리기 실패: %s", e)
        if settings.get("job_mode") == "post":
            self.work.set_value("post")
            self._work_changed()
        self._post_sum()

    def _load_cfg(self):
        c = self.cfg
        self.stages["analyze"].set_on(c.get("analyze_on", True))
        self.bpm_mode.set_value(c.get("bpm_mode", "src"))
        self.bpm.setValue(int(c.get("bpm", 90)))
        self.feel.set_value("on" if c.get("src_feel") else "off")
        self.strength.setValue(max(0, min(100 // STRENGTH_STEP, int(c.get("strength", 0) or 0) // STRENGTH_STEP)))
        st = c.get("style", prompt.DEFAULT_STYLE)
        self.style.set_value([st if st in prompt.STYLE_BY else prompt.DEFAULT_STYLE])
        self._set_caption(c.get("caption", "") or "")
        self.len_mode.set_value(c.get("length_mode", "src"))
        self.secs.setValue(int(c.get("seconds", 60)))
        self.cand.set_value(str(c.get("candidates", hw.profile().candidates)))
        e = c.get("engine")
        if e in dict(ENGINES) and self.engine.btns[e].isEnabled():
            self.engine.set_value(e)
        self.fmt.set_value(c.get("fmt", "mp3"))
        self.out_dir = c.get("out_dir", "")
        self.refs = [f for f in (c.get("refs") or []) if f and Path(f).is_file()][: prompt.MAX_REFS]

    def _save_cfg(self):
        p = self.plan()
        import dataclasses as _dc
        settings.put("post", _dc.asdict(self.post_plan(all_on=True)))
        settings.put("post_on", {k: self.stages[k].on for k in ("edit", "sound", "inst", "fix")})
        settings.put("job_mode", self.work.value())
        settings.put("work", {
            "analyze_on": p.analyze_on, "bpm_mode": self.bpm_mode.value(), "bpm": p.bpm, "src_feel": p.src_feel, "strength": self.strength.value() * STRENGTH_STEP,
            "style": p.style, "caption": p.caption, "length_mode": self.len_mode.value(), "seconds": p.seconds,
            "candidates": p.candidates, "engine": self.engine.value(), "fmt": p.fmt, "out_dir": self.out_dir, "refs": self.refs,
        })

    def style_key(self) -> str:
        return (self.style.value() or [prompt.DEFAULT_STYLE])[0]

    def _custom(self) -> str:
        """직접 고친 지시문(스타일 지시문과 다를 때만), 아니면 빈 글."""
        t = " ".join(self.cap.toPlainText().split())
        return "" if not t or t == prompt.style_caption(self.style_key()) else t

    def _set_caption(self, text: str):
        """지시문 칸 채우기: text 가 비면 고른 스타일의 지시문."""
        self.cap.blockSignals(True)
        self.cap.setPlainText(text or prompt.style_caption(self.style_key()))
        self.cap.blockSignals(False)
        if hasattr(self, "sum"):
            self._sync()

    def _style_changed(self, _v):
        self._set_caption("")                     # 스타일을 바꾸면 그 스타일의 지시문으로(직접 고친 것은 버림)

    def plan(self) -> jobs.Plan:
        an = self.stages["analyze"].on
        has = bool(self.checked())
        return jobs.Plan(analyze_on=an, bpm_mode=self.bpm_mode.value(), bpm=self.bpm.value(),
                         length_mode=self.len_mode.value() if (an and has) else "set", seconds=self.secs.value(),
                         candidates=int(self.cand.value()), fmt=self.fmt.value(), out_dir=self.out_dir,
                         title=self.title.text().strip(), refs=list(self.refs), style=self.style_key(),
                         caption=self._custom(), src_feel=self.feel.value() == "on",
                         cpu_lm="1.7B" if hw.profile().mode == "cpu" else "off",
                         quality=prompt.ENGINE_QUALITY.get(self.engine.value(), "std"),
                         strength=self.strength.value() * STRENGTH_STEP if (an and has) else 0)

    def _out_dir(self) -> Path:
        return Path(self.out_dir) if self.out_dir else paths.default_out_dir()

    def pick_ref(self):
        fs, _ = QFileDialog.getOpenFileNames(self, f"음색 참고 음원(실제 악기 녹음, 최대 {prompt.MAX_REFS}개)",
                                             settings.get("last_in", ""), AUDIO_FILTER)
        if fs:
            self._set_refs(fs[: prompt.MAX_REFS])

    def _set_refs(self, fs):
        self.refs = list(fs)
        self._sync()

    def pick_out(self):
        d = QFileDialog.getExistingDirectory(self, "저장 폴더", str(self._out_dir()))
        if d:
            self.out_dir = d
            self._sync()

    # ================================================================ 상태 반영
    def _current(self):
        it = self.list.currentItem()
        return it.data(ROLE) if it is not None else (self.sources[0] if self.sources else None)

    def checked(self) -> list[jobs.Source]:
        return [s for s in self.sources if s.checked and not s.error]

    def _engine_text(self, p) -> str:
        """지금 쓸 엔진 구성(시안 A): 연주 담당 · 작곡 담당 · 음원 변환부 + 켜짐/설치 전."""
        e = self.engine.value()
        gen, lm, vae = ("XL" if e == "XL" else "shift3"), ("4B" if e == "4B" else "1.7B"), "대체"
        state = "켜짐" if self.eng.running() else ("꺼짐" if EG.installed() else "설치 전")
        return f"엔진({state}) · 연주 {gen} · 작곡 담당 {lm} · 변환부 {vae}"

    def _sync(self):
        p = self.plan()
        self.eng_lab.setText(self._engine_text(p))
        an = self.stages["analyze"]
        cs = self.checked()
        if not cs and not an.locked:                           # 원곡 없음 = 분석 잠금(체크 꺼짐 · 템포 · 음색 참고는 씀)
            an.set_locked(True)
        elif cs and an.locked:                                 # 원곡을 넣으면 자동으로 켬
            an.set_locked(False)
            an.set_on(True)
        self.feel.setEnabled(bool(cs) and an.on)
        use_src = an.on and bool(cs)
        st = prompt.STYLE_BY[p.style]
        self.strength.setEnabled(use_src)                       # 원곡 반영(v0.1.18) — 원곡이 있고 분석이 켜졌을 때만
        cov = p.strength > 0
        v = self.strength.value() * STRENGTH_STEP
        self.strength_lab.setText("안 씀" if v == 0 else f"{v}%" + (" 주의" if v >= 70 else ""))
        self.strength_lab.setStyleSheet(f"color:{WARN if v >= 70 and use_src else SUB};")
        self.bpm_mode.setEnabled(not cov)                       # 커버 = 원곡 템포·조성 그대로
        self.bpm.setEnabled(self.bpm_mode.value() == "set" and not cov)
        self.len_mode.setEnabled(use_src and not cov)           # 원곡이 없거나 안 쓰면 길이는 직접 · 커버 = 원곡 길이
        self.secs.setEnabled(p.length_mode == "set" and not cov)
        self.secs_lab.setText(mmss(self.secs.value()))
        an.set_summary((f"원곡 반영 {p.strength}%" if cov else ("템포 자동" if p.bpm_mode == "src" else f"{p.bpm} BPM"))
                       + (" · 원곡 느낌 참고" if p.src_feel else "") if cs else "원곡 없음 — 스타일만으로")
        len_txt = "원곡 길이" if (p.length_mode == "src" or cov) else mmss(p.seconds)
        custom = bool(p.caption)
        self.stages["generate"].set_summary(f"{st[1]}{'(직접 고침)' if custom else ''} · {len_txt} · 후보 {p.candidates}"
                                            + f" · 엔진 {self.engine.value()}"
                                            + (" · 음색 참고" if self.refs and not (p.src_feel and use_src) else ""))
        self._post_sum()
        if self.lm_note is not None:
            self.lm_note.setText({"off": "곡 구성이 단순해질 수 있음", "0.6B": "처음 쓸 때 받음",
                                  "1.7B": "메모리 부족이면 끈 채로"}.get(p.cpu_lm, ""))
            self.lm_note.setStyleSheet(f"color:{SUB_TXT}; font-size:11px;")
        self.cap_note.setText("직접 고침 — 스타일을 다시 고르면 지워집니다" if custom else f"자동(스타일: {st[1]})")
        self.cap_note.setStyleSheet(f"color:{WARN if custom else SUB_TXT}; font-size:11px;")
        self.b_reset.setEnabled(custom)
        cur = self._current()
        self.title.setPlaceholderText("비우면 원곡 이름 첫 단어" + (f" (예: {prompt.first_word(cur.name)})" if cur else ""))
        if p.src_feel and use_src:
            rt = "원곡 느낌 참고를 켜서 원곡을 씁니다"
        elif not self.refs:
            rt = ""
        else:
            rt = Path(self.refs[0]).name + (f" 외 {len(self.refs) - 1}개" if len(self.refs) > 1 else "")
        self.ref_lab.setText(self.ref_lab.fontMetrics().elidedText(rt, Qt.ElideMiddle, 250))
        self.ref_lab.setToolTip("\n".join(self.refs) if self.refs else
                                "선택 사항 · 같은 편성의 실제 연주 녹음만 넣으세요. 비우면 쓰지 않습니다.")
        self.out_lab.setText(self.out_lab.fontMetrics().elidedText(str(self._out_dir()), Qt.ElideMiddle, 250))
        self.stages["output"].set_summary((f"{p.title} · " if p.title else "") + f"{p.fmt.upper()} · {self._out_dir().name}")
        # 요약 · 예상 시간
        n_src = len(cs) if use_src else 1
        total = sum(p.target_seconds(s) for s in cs) if use_src else p.target_seconds(None)
        n_out = n_src * p.candidates
        txt = (f"원곡 {len(cs)}개 → " if use_src else "원곡 없이 → ") + f"결과 {n_out}곡 · 만들 길이 합계 {mmss(total * p.candidates)}"
        est = jobs.estimate(total * p.candidates, p.cpu_lm)
        txt += f" · 예상 약 {max(1, round(est / 60))}분" if est else " · 예상 시간은 첫 생성 후 표시"
        post = self.work.value() == "post"
        if post:                                               # 후보정: 파일 수만큼 결과 · 켠 칸
            po = self.post_plan()
            on = [n for n, f in (("악기", po.inst_on), ("고치기", po.fix_on), ("편집", po.edit_on), ("음향", po.sound_on)) if f()]
            txt = (f"파일 {len(cs)}개 → 결과 {len(cs)}개" if cs else "후보정할 파일을 원곡 칸에 넣으세요") \
                + (f" · {' → '.join(on)}" if on else " · 켠 후보정 없음")
        self.engine2.set_value(self.engine.value())            # 고치기 칸 엔진 = 새로 만들기 엔진(되살린 값 포함)
        self.sum.setText(txt)
        if self.mode == "setup":
            if post and not (self.post_plan().fix_on() or self.post_plan().inst_on()):
                self.state_lab.setText("")
            elif EG.installed():
                self.state_lab.setText("")
            else:
                self.state_lab.setText("생성 엔진 설치 필요 — [AUTO]를 누르면 먼저 설치합니다(처음 한 번).")
                self.state_lab.setStyleSheet(f"color:{WARN};")
        self.b_start.setEnabled(True)
        self.b_start.setVisible(self.mode != "run")           # 설정 = [AUTO] · 생성 중 = [취소]만 · 끝 = [폴더 열기][돌아가기]
        self.b_cancel.setVisible(self.mode != "setup")
        self.b_cmp.setEnabled(self.mode == "setup")
        self.test_w.setVisible(self.mode == "setup" and self.work.value() == "new")
        self.b_fit.setEnabled(self.mode == "setup" and bool(cs))
        self.b_voc.setEnabled(self.mode == "setup")
        if self.mode == "setup":
            self._save_cfg()

    def _sel_changed(self):
        self._show_analysis(self._current())
        self._wave_load()

    def _show_analysis(self, s):
        if s is None:
            self.a_info.setText("원곡 없음 — 스타일과 길이만으로 만듭니다.")
            return
        if s.error:
            self.a_info.setText(f"{s.name}: 읽을 수 없음 ({s.error})")
        elif s.analysis is None:
            self.a_info.setText(f"{s.name}: 분석 중…")
        else:
            a = s.analysis
            sure = "확실" if a.key_conf >= 0.5 else ("보통" if a.key_conf >= 0.15 else "불확실 — 조성은 쓰지 않음")
            moods = " · ".join(prompt.MOOD[m][0] for m in a.moods if m in prompt.MOOD)
            self.a_info.setText(self.a_info.fontMetrics().elidedText(s.name, Qt.ElideMiddle, COL_W - 40)
                                + f"\n템포 {a.bpm:g} BPM · {a.key_label}({sure}) · 길이 {mmss(a.duration)}"
                                + (f"\n분위기: {moods}" if moods else ""))

    # ================================================================ 원곡 넣기·빼기
    def add_paths(self, items):
        files = collect_audio(items)
        have = {s.path for s in self.sources}
        new = [f for f in files if f not in have]
        if not new:
            if items and not files:
                ui_style.notice(self, "음원 파일(MP3 · WAV · FLAC · M4A 등)만 넣을 수 있습니다.")
            return
        for f in new:
            s = jobs.Source(f)
            self.sources.append(s)
            it = QListWidgetItem()
            it.setData(ROLE, s)
            self.list.addItem(it)
        aj = jobs.AnalyzeJob(new, self)
        aj.done.connect(self._analyzed)
        aj.finished.connect(lambda aj=aj: self.ajobs.remove(aj) if aj in self.ajobs else None)
        self.ajobs.append(aj)
        aj.start()
        if self.list.currentRow() < 0:
            self.list.setCurrentRow(0)
        self._sync()

    def _analyzed(self, path, a, dur, err):
        for s in self.sources:
            if s.path == path:
                s.analysis, s.duration, s.error = a, dur, err
                if err:
                    s.checked = False
        self.list.viewport().update()
        self._sel_changed()
        self._sync()

    def remove_selected(self):
        if self.mode != "setup":
            return
        for it in self.list.selectedItems():
            s = it.data(ROLE)
            if s in self.sources:
                self.sources.remove(s)
            self.list.takeItem(self.list.row(it))
        self._sel_changed()
        self._sync()

    def clear_sources(self):
        for aj in self.ajobs:
            aj.stop = True
        self.sources.clear()
        self.list.clear()
        self._sel_changed()
        self._sync()

    def _menu(self, gpos):
        if self.mode != "setup":
            return
        m = QMenu(self)
        m.addAction("파일 추가…", self.add_files_dialog)
        m.addAction("폴더 추가…", self.add_folder_dialog)
        m.addSeparator()
        a = m.addAction("선택 제거", self.remove_selected)
        a.setEnabled(bool(self.list.selectedItems()))
        m.addAction("모두 제거", self.clear_sources).setEnabled(bool(self.sources))
        m.exec(gpos)

    def add_files_dialog(self):
        fs, _ = QFileDialog.getOpenFileNames(self, "원곡 추가", settings.get("last_in", ""), AUDIO_FILTER)
        if fs:
            settings.put("last_in", str(Path(fs[0]).parent))
            self.add_paths(fs)

    def add_folder_dialog(self):
        d = QFileDialog.getExistingDirectory(self, "폴더 추가", settings.get("last_in", ""))
        if d:
            settings.put("last_in", d)
            self.add_paths([d])

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and self.mode == "setup":
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.add_paths([u.toLocalFile() for u in e.mimeData().urls()])

    # ================================================================ 실행
    def _ensure_engine(self) -> bool:
        if EG.installed():
            return True
        from .ui_engine import EngineDialog
        dlg = EngineDialog(self)
        c = self.geometry().center()
        dlg.move(c.x() - dlg.width() // 2, c.y() - dlg.height() // 2)
        dlg.exec()
        self._sync()
        return EG.installed()

    def start(self):
        if self.mode == "done":
            self.back_to_setup()
            return
        if self.mode != "setup":
            return
        self._run(test=False)                     # 원곡이 없어도 됨(스타일 · 길이만으로)

    def start_compare(self):
        if self.mode == "setup":
            self._run(test=False, compare=True)

    def start_redo(self, path: str):
        """결과 줄 [다시 입히기]: 작업이 끝난 뒤에만(생성 중엔 무시)."""
        if self.mode == "done" and path and Path(path).is_file():
            self._run(test=False, redo=path)

    def start_vocal(self):
        if self.mode == "setup":
            self._run(test=False, vocal=True)

    def start_fit(self):
        """원곡 길이 맞추기 시험: 첫 원곡을 '길이' 칸의 값으로."""
        if self.mode != "setup":
            return
        cs = self.checked()
        if not cs:
            ui_style.notice(self, "길이를 맞출 원곡이 없습니다. 원곡을 넣고 체크하세요.")
            return
        if self.len_mode.value() != "set":
            ui_style.notice(self, "'길이'를 [직접]으로 두고 맞출 길이(초)를 넣은 뒤 다시 누르세요.")
            return
        self._run(test=False, fit_len=True)

    def _confirm_models(self, compare: bool) -> bool:
        """필요한 모델이 없으면 내려받아도 되는지 묻기(크기 · 남은 공간). 내려받기는 작업 안에서."""
        need = jobs.needed_models(self.plan().quality)
        if not need:
            return True
        gb = sum(prompt.MODEL_INFO[m][1] for m in need)
        free = hw.disk_free_gb(EG.checkpoints().parent)
        names = ", ".join(prompt.MODEL_INFO[m][0] for m in need)
        if free and free < gb + 5:
            ui_style.notice(self, f"모델을 내려받을 공간이 부족합니다.\n{names}\n필요 약 {gb}GB · 남은 공간 {free:g}GB")
            return False
        r = ui_style.confirm(self, f"모델을 먼저 내려받습니다(처음 한 번).\n{names}\n약 {gb}GB"
                             + (f" · 남은 공간 {free:g}GB" if free else ""),
                             [("cancel", "취소", 72, None), ("ok", "내려받고 시작", 110, "blue")])
        return r == "ok"

    def post_plan(self, all_on: bool = False) -> "PO.Post":
        """후보정 화면 설정 → Post. 칸 머리 체크를 끈 칸은 비움(all_on = 체크 무시, 요약용)."""
        P = self.post
        on = {k: all_on or self.stages[k].on for k in ("edit", "sound", "inst", "fix")}
        po = PO.Post()
        if on["edit"]:
            po.cut, po.fade_in, po.fade_out = list(P["cut"].value()), P["fade_in"].value(), P["fade_out"].value()
            fs = P["fit_s"].value()
            po.fit, po.fit_s = ("set" if fs > P["fit_s"].minimum() else "keep"), fs
            po.tempo, po.key = P["tempo"].value(), P["key"].value()
        if on["sound"]:
            po.denoise = P["denoise"].value() * 2
            po.low, po.high = P["low"].value() / 2.0, P["high"].value() / 2.0
            po.clean = list(P["clean"].value())
        if on["inst"]:
            for k in ("vocals", "drums", "bass", "other"):
                db = self.INST_DB[P[k].value()]
                setattr(po, k, PO.MUTE if db is None else float(db))
            po.stems = bool(P["stems"].value())
        if on["fix"]:
            po.repaint, po.rp_a, po.rp_b = bool(P["repaint"].value()), P["rp_a"].value(), P["rp_b"].value()
            po.redo = bool(P["redo"].value())
            po.extend, po.ext_s = bool(P["extend"].value()), P["ext_s"].value()
        return po

    def _run(self, test: bool, compare: bool = False, fit_len: bool = False, vocal: bool = False, redo: str = ""):
        post = self.post_plan() if (self.work.value() == "post" and not (test or compare or fit_len or vocal or redo)) \
            else None
        if post is not None:
            if not self.checked():
                ui_style.notice(self, "후보정할 파일을 원곡 칸에 넣으세요(만든 곡을 다시 넣어도 됩니다).")
                return
            if not post.any_on():
                ui_style.notice(self, "켠 후보정이 없습니다.\n편집 · 음향 · 악기 · 고치기에서 필요한 것을 켜세요.")
                return
            if (post.fix_on() or post.inst_on()) and not self._ensure_engine():
                return
        else:
            if not self._ensure_engine():
                return
            if not self._confirm_models(compare):
                return
        self.player.stop()
        plan = self.plan()
        self._save_cfg()
        self.job = jobs.MakeJob(self.eng, self.checked(), plan, test=test, compare=compare, fit_len=fit_len,
                                vocal=vocal, redo=redo, post=post, parent=self)
        self.job.step.connect(self._on_step)
        self.job.item.connect(self._on_item)
        self.job.finished_ok.connect(self._on_done)
        self._clear_results()
        self.step_bar.set_steps(self.job.steps())
        self._step_key, self._step_t0 = "", time.monotonic()
        self.mode = "run"
        self.rw.setVisible(False)
        self.run_w.setVisible(True)
        self.b_cancel.setText("취소")
        self.b_cancel.setEnabled(True)
        self.state_lab.setText("")
        self.run_lab.run("시작하는 중")
        self.t0 = time.monotonic()
        self._sync()
        self.job.start()

    def _tick(self):
        """1초마다: 엔진 준비 중이면 막대 글에 경과 시간(진행률을 알 수 없는 단계라 멈춘 게 아님을 보여 줌)."""
        if self.mode != "run":
            return
        k = getattr(self, "_step_key", "")
        if k == "engine":
            self.step_bar.set("engine", 0.0, f"경과 {mmss(time.monotonic() - self._step_t0)}")

    def _on_step(self, key, frac, text):
        if key != getattr(self, "_step_key", ""):
            self._step_key, self._step_t0 = key, time.monotonic()
        self.step_bar.set(key, frac, f"경과 {mmss(time.monotonic() - self._step_t0)}" if key == "engine" else "")
        if text:
            self.run_lab.run(text)
        f = self.step_bar.frac()
        self.setWindowTitle(f"DTMC {__version__} — {int(f * 100)}%")
        taskbar.set_progress(self, f)

    def _on_item(self, info):
        row = ResultRow(info)
        row.play.connect(self.player.toggle)
        row.play_src.connect(self.player.toggle_src)
        row.redo.connect(lambda r: self.start_redo(r.path))
        self.res_box.insertWidget(self.res_box.count() - 1, row)
        QTimer.singleShot(50, lambda: self.res_scroll.verticalScrollBar().setValue(
            self.res_scroll.verticalScrollBar().maximum()))

    def _clear_results(self):
        while self.res_box.count() > 1:
            w = self.res_box.takeAt(0).widget()
            if w is not None:
                w.deleteLater()

    def _on_done(self, ok, msg):
        outs = list(self.job.outputs) if self.job is not None else []
        self._compare = bool(self.job is not None and self.job.compare)
        self._fit = bool(self.job is not None and self.job.fit_len)
        self.job = None
        self.mode = "done"
        self.setWindowTitle(f"DTMC {__version__}")
        taskbar.clear(self)
        taskbar.flash(self)
        if ok:
            self.step_bar.finish(msg)
            self.run_lab.halt(f"완료 · {msg}" + (" · 들어 보고 쓸 만한 스타일을 알려 주세요" if self._compare else
                                                " · 원곡을 그대로 쓴 결과입니다 — 공개 영상이면 원곡 권리를 확인하세요"
                                                if self._fit else ""))
        else:
            self.run_lab.halt(msg)
            self.run_lab.setStyleSheet(f"color:{WARN};")
        self.b_cancel.setText("폴더 열기")
        self.b_cancel.setEnabled(bool(outs) or ok)
        self.b_start.setText("돌아가기")
        ui_style.tone(self.b_start, "blue")
        self._sync()
        if ok:
            r = ui_style.confirm(self, f"끝났습니다 — {msg}", [("folder", "폴더 열기", 92, None), ("ok", "확인", 80, "blue")])
            if r == "folder":
                self._open_outputs(outs)
        elif "취소" not in msg:
            ui_style.notice(self, f"만들지 못했습니다.\n{msg}\n\n자세한 기록: {paths.log_dir()}")

    def _open_outputs(self, outs=None):
        outs = outs or [w.path for w in self.findChildren(ResultRow)]
        if outs:
            open_folder(outs[-1], select=True)
        else:
            open_folder(self._out_dir())

    def back_to_setup(self):
        self.player.stop()
        self.mode = "setup"
        self.run_w.setVisible(False)
        self.rw.setVisible(True)
        self.run_lab.setStyleSheet(f"color:{SUB};")
        self.b_cancel.setText("취소")
        self.b_start.setText("AUTO")
        ui_style.tone(self.b_start, "red")
        self._sync()

    def cancel(self):
        if self.mode == "run":
            if self.job is not None:
                self.job.cancel = True
                self.b_cancel.setEnabled(False)
                self.run_lab.run("취소하는 중")
            return
        if self.mode == "done":
            self._open_outputs()

    def busy(self) -> bool:
        return self.mode == "run"

    def request_quit(self):
        """[✕]: 생성 중이면 확인 → 멈추고 종료. 엔진·하위 프로세스는 종료 정리(main._cleanup)가 끔."""
        if self.mode == "run":
            r = ui_style.confirm(self, "생성 중입니다. 멈추고 끌까요?", [("cancel", "계속", 72, None), ("quit", "끄기", 80, "red")])
            if r != "quit":
                return
            if self.job is not None:
                self.job.cancel = True
        self.player.stop()
        self.hide()
        from PySide6.QtWidgets import QApplication
        QApplication.instance().quit()

    def closeEvent(self, e):
        e.ignore()
        self.request_quit()


def QPushButton_close(win) -> QWidget:
    """작업 창 오른쪽 위 [✕] = 프로그램 종료(생성 중이면 묻고 멈춤)."""
    from PySide6.QtWidgets import QPushButton
    b = QPushButton("✕")
    b.setToolTip("종료")
    b.setFocusPolicy(Qt.NoFocus)
    b.setFixedSize(26, 22)
    b.setStyleSheet("QPushButton{color:#ddd; background:transparent; border:none; font-size:14px;}"
                    "QPushButton:hover{background:#c0392b; color:#fff;}")
    b.clicked.connect(win.request_quit)
    return b


def QPushButton_min(win) -> QWidget:
    """작업 창 오른쪽 위 [−](내리기)."""
    from PySide6.QtWidgets import QPushButton
    b = QPushButton("−")
    b.setToolTip("내리기")
    b.setFocusPolicy(Qt.NoFocus)
    b.setFixedSize(26, 22)
    b.setStyleSheet("QPushButton{color:#ddd; background:transparent; border:none; font-size:16px;}"
                    "QPushButton:hover{background:#3a3a3a;}")
    b.clicked.connect(win.showMinimized)
    return b

