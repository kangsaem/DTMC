"""작업 창(DTME 자동 창 틀): 왼쪽 = 원곡 목록, 오른쪽 = 단계 줄 3개(분석 · 생성 · 출력), 아래 = [취소][AUTO].
[AUTO] = 작업 화면(단계 막대 + 결과 줄, 미리듣기) → 끝나면 [폴더 열기][확인] → [돌아가기] = 설정 화면.
처음 [AUTO] 때 엔진이 없으면 설치 창(ui_engine)부터."""
from __future__ import annotations

import os
import time
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMenu, QScrollArea, QSizePolicy, QSpinBox, QStyle, QStyledItemDelegate,
                               QVBoxLayout, QWidget)

from . import __version__, engine as EG, ff, hw, jobs, log, paths, prompt, settings, taskbar, ui_style
from .ui_widgets import (CHK_BG, SUB, SUB_TXT, WARN, Busy, DragMove, Grip, Multi, Seg, StageRow, StepBar,
                         labeled, main_button, no_hfw, open_folder, small_button)

L = log.log
EMPTY_HINT = ("원곡(음원 파일) · 폴더를 여기에 끌어다 놓기", "우클릭 = 파일 추가 · 폴더 추가")
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
    play = Signal(object)

    def __init__(self, info: dict, parent=None):
        super().__init__(parent)
        self.info = info
        self.path = info["path"]
        self.setObjectName("ResultRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("#ResultRow{background:#2a2a2a; border:1px solid #3a3a3a; border-radius:3px;}"
                           "QLabel{background:transparent;}")
        self.setFixedHeight(44)
        self.b_play = small_button("▶", 30, 28, "미리듣기")
        self.b_play.clicked.connect(lambda: self.play.emit(self))
        name = QLabel(Path(self.path).name)
        name.setStyleSheet("color:#e6e6e6;")
        name.setToolTip(self.path)
        self.name = name
        sub = QLabel(f"{info.get('source', '')} · {info.get('instr', '')} · {mmss(info.get('seconds', 0))}")
        sub.setStyleSheet(f"color:{SUB_TXT}; font-size:11px;")
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(name)
        col.addWidget(sub)
        b_dir = small_button("폴더", 48, 26, "저장한 곳 열기")
        b_dir.clicked.connect(lambda: open_folder(self.path, select=True))
        h = QHBoxLayout(self)
        h.setContentsMargins(8, 4, 8, 4)
        h.setSpacing(8)
        h.addWidget(self.b_play)
        h.addLayout(col, 1)
        h.addWidget(b_dir)

    def set_playing(self, on: bool):
        self.b_play.setText("■" if on else "▶")
        self.b_play.setChecked(False)
        ui_style.tone(self.b_play, "blue" if on else "")


class Player:
    """미리듣기(QtMultimedia). 못 쓰면 None 처럼 동작(▶ = 파일 열기)."""

    def __init__(self, parent):
        self.parent = parent
        self.cur: ResultRow | None = None
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
        if self._ensure() is None:
            open_folder(row.path)
            return
        if self.cur is row:
            self.stop()
            return
        self.stop()
        self.cur = row
        self.mp.setSource(QUrl.fromLocalFile(row.path))
        self.mp.play()
        row.set_playing(True)

    def stop(self):
        if self.mp is not None:
            self.mp.stop()
        if self.cur is not None:
            try:
                self.cur.set_playing(False)
            except RuntimeError:
                pass
        self.cur = None

    def _state(self, st):
        from PySide6.QtMultimedia import QMediaPlayer
        if st == QMediaPlayer.StoppedState and self.cur is not None:
            try:
                self.cur.set_playing(False)
            except RuntimeError:
                pass
            self.cur = None


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
        self.cfg = settings.get("work", {}) or {}
        self.player = Player(self)
        self._step_key, self._step_t0 = "", time.monotonic()
        self._ticker = QTimer(self)
        self._ticker.setInterval(1000)
        self._ticker.timeout.connect(self._tick)
        self._ticker.start()
        self._build()
        self._load_cfg()
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
        lcol = QVBoxLayout()
        lcol.setSpacing(4)
        lcol.addWidget(self.list, 1)
        # ---- 오른쪽: 실행 모드 · 요약 · 단계 3줄
        pr = hw.profile()
        self.mode_lab = QLabel(pr.label)
        self.mode_lab.setStyleSheet(f"color:{SUB_TXT};")
        self.warn_lab = no_hfw(QLabel(pr.warn), 2)
        self.warn_lab.setStyleSheet(f"color:{WARN};")
        self.warn_lab.setVisible(bool(pr.warn))
        self.sum = no_hfw(QLabel(""), 2)
        self.sum.setStyleSheet(f"color:{SUB};")
        self.stages = {
            "analyze": StageRow("analyze", self._analyze_body()),
            "generate": StageRow("generate", self._generate_body(), fixed=True),
            "output": StageRow("output", self._output_body(), fixed=True),
        }
        for sr in self.stages.values():
            sr.toggled.connect(lambda _k, _v: self._mood_mode())
            sr.clicked.connect(self._open_stage)
        right = QVBoxLayout()
        right.setSpacing(8)
        right.setContentsMargins(0, 0, 0, 0)
        right.addWidget(self.mode_lab)
        right.addWidget(self.warn_lab)
        right.addWidget(self.sum)
        right.addStretch(1)
        for k in ("analyze", "generate", "output"):
            right.addWidget(self.stages[k])
        self.rw_in = Panel()
        self.rw_in.setLayout(right)
        self.rw = QScrollArea()                   # 단계 줄을 펼쳐 칸보다 길어지면 스크롤(창 크기는 그대로)
        self.rw.setWidgetResizable(True)
        self.rw.setWidget(self.rw_in)
        self.rw.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.rw.setFrameShape(QScrollArea.NoFrame)
        self.rw.setStyleSheet("QScrollArea{background:transparent;} QScrollArea > QWidget > QWidget{background:transparent;}")
        self.rw.setFixedWidth(470)
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
        self.run_w.setFixedWidth(470)
        self.run_w.setVisible(False)
        mid = QHBoxLayout()
        mid.setSpacing(16)
        mid.addLayout(lcol, 1)
        mid.addWidget(self.rw)
        mid.addWidget(self.run_w)
        # ---- 아래
        self.ver_lab = QLabel(f"DTMC {__version__}")
        self.ver_lab.setStyleSheet("color:#6f6f6f; font-size:11px;")
        self.state_lab = QLabel("")
        self.b_cancel = main_button("취소")
        self.b_cancel.clicked.connect(self.cancel)
        self.b_start = main_button("AUTO", "red")
        self.b_start.clicked.connect(self.start)
        bot = QHBoxLayout()
        bot.setSpacing(8)
        bot.addWidget(self.ver_lab)
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
        self.resize(1040, 720)

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
        self.a_info = no_hfw(QLabel("원곡을 넣으면 템포 · 조성 · 분위기를 자동으로 찾습니다."), 3)
        self.a_info.setStyleSheet(f"color:{SUB};")
        self.mood_mode = Seg([("auto", "원곡대로"), ("set", "직접 고르기")], "auto")
        self.mood_mode.changed.connect(self._mood_mode)
        self.moods = Multi([(k, ko) for k, ko, _ in prompt.MOODS], ["calm"], limit=3, cols=4, min_one=True)
        self.moods.changed.connect(self._moods_changed)
        self.manual_moods = ["calm"]
        self.bpm_mode = Seg([("src", "원곡 템포"), ("set", "직접")], "src")
        self.bpm_mode.changed.connect(lambda _k: self._sync())
        self.bpm = QSpinBox()
        self.bpm.setRange(40, 200)
        self.bpm.setSuffix(" BPM")
        self.bpm.setValue(90)
        self.bpm.setFixedWidth(96)
        self.bpm.valueChanged.connect(lambda _v: self._sync())
        brow = QHBoxLayout()
        brow.setSpacing(6)
        brow.addWidget(self.bpm_mode, 1)
        brow.addWidget(self.bpm)
        return self._body([self.a_info, labeled("분위기", self.mood_mode), labeled("", self.moods),
                           labeled("템포", brow),
                           self._hint("끄면 분석 없이 고른 악기·분위기만으로 만듭니다(분위기 = 직접 고른 것, 없으면 차분함).")])

    def _generate_body(self) -> QWidget:
        self.instr = Multi([(k, ko) for k, ko, _ in prompt.INSTRUMENTS], ["piano"], limit=prompt.MAX_INSTR, cols=4)
        self.instr.changed.connect(lambda _v: self._sync())
        self.instr_note = self._hint("최대 3개까지 함께 쓸 수 있습니다.")
        self.instr.refused.connect(self._instr_refused)
        self.len_mode = Seg([("src", "원곡 길이"), ("set", "직접")], "src")
        self.len_mode.changed.connect(lambda _k: self._sync())
        self.secs = QSpinBox()
        self.secs.setRange(prompt.LEN_MIN, prompt.LEN_MAX)
        self.secs.setSingleStep(10)
        self.secs.setSuffix(" 초")
        self.secs.setValue(60)
        self.secs.setFixedWidth(96)
        self.secs.valueChanged.connect(lambda _v: self._sync())
        self.secs_lab = QLabel("")
        self.secs_lab.setFixedWidth(40)
        self.secs_lab.setStyleSheet(f"color:{SUB_TXT};")
        lrow = QHBoxLayout()
        lrow.setSpacing(6)
        lrow.addWidget(self.len_mode, 1)
        lrow.addWidget(self.secs)
        lrow.addWidget(self.secs_lab)
        self.strength = Seg(prompt.STRENGTHS, "low")
        self.strength.changed.connect(lambda _k: self._sync())
        self.str_hint = self._hint("")
        self.cand = Seg([(str(i), f"{i}곡") for i in (1, 2, 3, 4)], str(hw.profile().candidates))
        self.cand.changed.connect(lambda _k: self._sync())
        self.b_test = small_button("악기 시험", 88, 28, "8개 악기를 20초씩 만들어 음질을 비교")
        self.b_test.clicked.connect(self.start_test)
        trow = QHBoxLayout()
        trow.setSpacing(8)
        trow.addWidget(self.b_test)
        trow.addWidget(self._hint("악기 8개를 20초씩 만들어 음질을 확인합니다(원곡 필요 없음)."), 1)
        return self._body([labeled("악기", self.instr), labeled("", self.instr_note), labeled("길이", lrow),
                           labeled("원곡 반영", self.strength), labeled("", self.str_hint),
                           labeled("후보", self.cand), trow])

    def _output_body(self) -> QWidget:
        self.fmt = Seg(ff.FORMAT_NAMES, "mp3")
        self.fmt.changed.connect(lambda _k: self._sync())
        self.out_lab = QLabel("")
        self.out_lab.setStyleSheet(f"color:{SUB};")
        self.out_lab.setMinimumWidth(1)
        self.out_lab.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.out_dir = ""
        b_ch = small_button("변경", 52)
        b_ch.clicked.connect(self.pick_out)
        b_open = small_button("열기", 52)
        b_open.clicked.connect(lambda: open_folder(self._out_dir()))
        orow = QHBoxLayout()
        orow.setSpacing(6)
        orow.addWidget(self.out_lab, 1)
        orow.addWidget(b_ch)
        orow.addWidget(b_open)
        return self._body([labeled("형식", self.fmt), labeled("저장 폴더", orow),
                           self._hint("파일 이름 = 원곡이름_악기(_번호). 같은 이름이 있으면 (2)를 붙입니다.")])

    # ================================================================ 설정 기억
    def _load_cfg(self):
        c = self.cfg
        self.stages["analyze"].set_on(c.get("analyze_on", True))
        self.mood_mode.set_value(c.get("mood_mode", "auto"))
        self.manual_moods = list(c.get("moods", ["calm"]) or ["calm"])
        self.bpm_mode.set_value(c.get("bpm_mode", "src"))
        self.bpm.setValue(int(c.get("bpm", 90)))
        self.instr.set_value(c.get("instruments", ["piano"]) or ["piano"])
        self.len_mode.set_value(c.get("length_mode", "src"))
        self.secs.setValue(int(c.get("seconds", 60)))
        self.strength.set_value(c.get("strength", "low"))
        self.cand.set_value(str(c.get("candidates", hw.profile().candidates)))
        self.fmt.set_value(c.get("fmt", "mp3"))
        self.out_dir = c.get("out_dir", "")
        self._mood_mode()
        op = c.get("open", "generate")
        if op in self.stages:
            self.stages[op].set_open(True)

    def _save_cfg(self):
        p = self.plan()
        settings.put("work", {
            "analyze_on": p.analyze_on, "mood_mode": self.mood_mode.value(), "moods": self.manual_moods,
            "bpm_mode": p.bpm_mode, "bpm": p.bpm, "instruments": p.instruments, "length_mode": p.length_mode,
            "seconds": p.seconds, "strength": p.strength, "candidates": p.candidates, "fmt": p.fmt,
            "out_dir": self.out_dir,
            "open": next((k for k, s in self.stages.items() if s.open_), ""),
        })

    def plan(self) -> jobs.Plan:
        an = self.stages["analyze"].on
        moods = list(self.manual_moods) if (self.mood_mode.value() == "set" or not an) else []
        return jobs.Plan(analyze_on=an, moods=moods, bpm_mode=self.bpm_mode.value() if an else "set",
                         bpm=self.bpm.value(), instruments=self.instr.value() or ["piano"],
                         length_mode=self.len_mode.value(), seconds=self.secs.value(), strength=self.strength.value(),
                         candidates=int(self.cand.value()), fmt=self.fmt.value(), out_dir=self.out_dir)

    def _out_dir(self) -> Path:
        return Path(self.out_dir) if self.out_dir else paths.default_out_dir()

    def pick_out(self):
        d = QFileDialog.getExistingDirectory(self, "저장 폴더", str(self._out_dir()))
        if d:
            self.out_dir = d
            self._sync()

    # ================================================================ 상태 반영
    def _open_stage(self, key):
        for k, sr in self.stages.items():
            sr.set_open(k == key and not sr.open_)
        self._save_cfg()

    def _mood_mode(self, _k=None):
        """원곡대로 = 고른 원곡의 자동 분위기를 보여 줌(누를 수 없음) · 직접 = 내가 고른 것."""
        if self.mood_mode.value() == "set" or not self.stages["analyze"].on:
            self.moods.set_value(self.manual_moods)
        else:
            s = self._current()
            self.moods.set_value(s.analysis.moods if s is not None and s.analysis is not None else [])
        self._sync()

    def _moods_changed(self, v):
        if self.mood_mode.value() == "set" or not self.stages["analyze"].on:
            self.manual_moods = list(v) or ["calm"]
        self._sync()

    def _current(self):
        it = self.list.currentItem()
        return it.data(ROLE) if it is not None else (self.sources[0] if self.sources else None)

    def _instr_refused(self):
        self.instr_note.setStyleSheet(f"color:{WARN}; font-size:11px;")
        QTimer.singleShot(1500, lambda: self.instr_note.setStyleSheet(f"color:{SUB_TXT}; font-size:11px;"))

    def checked(self) -> list[jobs.Source]:
        return [s for s in self.sources if s.checked and not s.error]

    def _sync(self):
        p = self.plan()
        an = self.stages["analyze"]
        self.moods.set_enabled_all(self.mood_mode.value() == "set" or not an.on)
        self.bpm.setEnabled(self.bpm_mode.value() == "set" or not an.on)
        self.secs.setEnabled(p.length_mode == "set")
        self.secs_lab.setText(mmss(self.secs.value()))
        mood_txt = ("분위기 자동" if not p.moods else " · ".join(prompt.MOOD[m][0] for m in p.moods))
        an.set_summary(f"{mood_txt} · " + ("원곡 템포" if p.bpm_mode == "src" else f"{p.bpm} BPM"))
        len_txt = "원곡 길이" if p.length_mode == "src" else mmss(p.seconds)
        st_name = dict(prompt.STRENGTHS)[p.strength]
        self.stages["generate"].set_summary(f"{prompt.instr_label(p.instruments)} · {len_txt} · 반영 {st_name} · 후보 {p.candidates}")
        self.str_hint.setText({"low": "원곡 소리는 보내지 않고 템포·조성·분위기만 참고합니다(저작권 안전, 기본).",
                               "mid": "원곡 소리를 약하게 참고합니다(흐름이 조금 닮음).",
                               "high": "원곡 구조를 많이 따라갑니다. 판매·공개용이면 원곡과 너무 닮지 않았는지 꼭 확인하세요."}[p.strength])
        self.str_hint.setStyleSheet(f"color:{WARN if p.strength == 'high' else SUB_TXT}; font-size:11px;")
        self.out_lab.setText(self.out_lab.fontMetrics().elidedText(str(self._out_dir()), Qt.ElideMiddle, 250))
        self.stages["output"].set_summary(f"{p.fmt.upper()} · {self._out_dir().name}")
        # 요약 · 예상 시간
        cs = self.checked()
        total = sum(p.target_seconds(s) for s in cs)
        n_out = len(cs) * p.candidates
        if cs:
            txt = f"원곡 {len(cs)}개 → 결과 {n_out}곡 · 만들 길이 합계 {mmss(total * p.candidates)}"
            est = jobs.estimate(total * p.candidates)
            txt += f" · 예상 약 {max(1, round(est / 60))}분" if est else " · 예상 시간은 첫 생성 후 표시"
        else:
            txt = "원곡을 넣어 주세요."
        self.sum.setText(txt)
        if self.mode == "setup":
            if EG.installed():
                self.state_lab.setText("")
            else:
                self.state_lab.setText("생성 엔진 설치 필요 — [AUTO]를 누르면 먼저 설치합니다(처음 한 번).")
                self.state_lab.setStyleSheet(f"color:{WARN};")
        self.b_start.setEnabled(self.mode == "done" or bool(cs))
        self.b_start.setVisible(self.mode != "run")           # 설정 = [AUTO] · 생성 중 = [취소]만 · 끝 = [폴더 열기][돌아가기]
        self.b_cancel.setVisible(self.mode != "setup")
        self.b_test.setEnabled(self.mode == "setup")
        if self.mode == "setup":
            self._save_cfg()

    def _sel_changed(self):
        self._show_analysis(self._current())
        if self.mood_mode.value() == "auto" and self.stages["analyze"].on:
            s = self._current()
            self.moods.set_value(s.analysis.moods if s is not None and s.analysis is not None else [])

    def _show_analysis(self, s):
        if s is None:
            self.a_info.setText("원곡을 넣으면 템포 · 조성 · 분위기를 자동으로 찾습니다.")
            return
        if s.error:
            self.a_info.setText(f"{s.name}: 읽을 수 없음 ({s.error})")
        elif s.analysis is None:
            self.a_info.setText(f"{s.name}: 분석 중…")
        else:
            a = s.analysis
            sure = "확실" if a.key_conf >= 0.5 else ("보통" if a.key_conf >= 0.15 else "불확실 — 조성은 쓰지 않음")
            moods = " · ".join(prompt.MOOD[m][0] for m in a.moods if m in prompt.MOOD)
            self.a_info.setText(f"{s.name}\n템포 {a.bpm:g} BPM · {a.key_label}({sure}) · 길이 {mmss(a.duration)}\n"
                                f"분위기(자동): {moods}")

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
        if not self.checked():
            ui_style.notice(self, "만들 원곡이 없습니다. 원곡을 넣고 체크하세요.")
            return
        self._run(test=False)

    def start_test(self):
        if self.mode == "setup":
            self._run(test=True)

    def _run(self, test: bool):
        if not self._ensure_engine():
            return
        self.player.stop()
        plan = self.plan()
        self._save_cfg()
        self.job = jobs.MakeJob(self.eng, self.checked(), plan, test=test, parent=self)
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
        self.job = None
        self.mode = "done"
        self.setWindowTitle(f"DTMC {__version__}")
        taskbar.clear(self)
        taskbar.flash(self)
        if ok:
            self.step_bar.finish(msg)
            self.run_lab.halt(f"완료 · {msg}")
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

