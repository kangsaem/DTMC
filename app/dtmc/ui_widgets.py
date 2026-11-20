"""공통 화면 부품 — DTME 자동 창(ui_autowin·ui_auto)과 같은 모양·크기.
Grip(손잡이) · Seg([a|b] 하나만) · Multi(여러 개 켬, 최대 n) · StageRow(단계 줄) · StepBar(진행 막대) · DragMove · labeled."""
from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QAbstractSpinBox, QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

from . import ui_style

SUB = "#c4cad1"                # 보조 글자(흐린 흰색)
SUB_TXT = "#8f9499"
CHK_BG = "#1e1e1e"
WARN = "#f2c94c"
MAIN_W, MAIN_H = 88, 32
# 단계: (줄 채움, 밝은 색 = 막대·글자, 이름) — DTME stage.STAGE 색을 그대로 씀(분석 = 청록, 음악 = 분홍, 렌더 = 빨강, 준비 = 파랑)
STAGE = {
    "engine": ("#2f3a4a", "#4a8fe0", "엔진 준비"),
    "analyze": ("#24443e", "#3fb8a0", "분석"),
    "generate": ("#4a2c40", "#d97fb8", "생성"),
    "output": ("#4a2e2c", "#d9604f", "출력"),
    # 후보정(시안 I): 편집 · 음향 · 악기 · 고치기
    "edit": ("#4a3f2c", "#d9a54a", "편집"),
    "sound": ("#2c3f4a", "#4ab0d9", "음향"),
    "inst": ("#3a2c4a", "#a07fd9", "악기"),
    "fix": ("#2c4a33", "#5fc27a", "고치기"),
}


def name_of(key: str) -> str:
    return STAGE[key][2]


def color_of(key: str) -> str:
    return STAGE[key][1]


class DragMove:
    """테두리 없는 창: 빈 바탕을 잡고 끌면 창이 움직임(DTME·MOSCO 와 같음)."""
    _drag_off = None

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_off = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_off is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_off)
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_off = None
        super().mouseReleaseEvent(e)


def bring_front(w):
    """창을 맨 앞으로(윈도우는 잠깐 '항상 위' 켰다 끔 — DTME taskbar.front 방식 축약)."""
    try:
        if w.isMinimized():
            w.showNormal()
        w.raise_()
        w.activateWindow()
        if sys.platform == "win32":
            import ctypes
            h = int(w.winId())
            u = ctypes.windll.user32
            u.SetWindowPos(h, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)      # TOPMOST, NOSIZE|NOMOVE|SHOWWINDOW
            u.SetWindowPos(h, -2, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)      # NOTOPMOST
            u.SetForegroundWindow(h)
    except Exception:  # noqa: BLE001
        pass


def open_folder(path, select: bool = False):
    p = str(path)
    try:
        if sys.platform == "win32":
            if select:
                from . import procs
                procs.launch_outside(["explorer", "/select,", os.path.normpath(p)])   # DTMC 를 꺼도 탐색기는 남게
            else:
                os.startfile(p)                                           # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R" if select else "", p])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(p) if select else p])
    except Exception:  # noqa: BLE001
        pass


def main_button(text, tone=None, w=MAIN_W):
    b = QPushButton(text)
    b.setFixedSize(w, MAIN_H)
    b.setFocusPolicy(Qt.NoFocus)
    if tone:
        ui_style.tone(b, tone)
    return b


def small_button(text, w=None, h=26, tip=""):
    b = QPushButton(text)
    b.setFocusPolicy(Qt.NoFocus)
    b.setFixedHeight(h)
    if w:
        b.setFixedWidth(w)
    if tip:
        b.setToolTip(tip)
    return b


BTN_H = 30                    # 버튼 규칙(시안): 큰 버튼(AUTO류) = MAIN_H · 나머지 버튼 · 숫자 칸 = 모두 30
GAP = 5                       # 버튼 사이 간격 = 스타일 격자 · Seg 와 같음
TEST_W = 120                  # 아래 줄 시험 버튼 폭(모두 같게)


def grid4(items) -> QWidget:
    """설정 칸 한 줄 = 4칸 격자. items = [(위젯 | None, 차지할 칸 수)] — 버튼은 1칸 또는 2칸, Seg 는 버튼 수만큼 칸.
    격자 간격 = Seg 간격(5) 이라 줄마다 버튼 경계가 스타일 격자와 맞음."""
    w = QWidget()
    g = QGridLayout(w)
    g.setContentsMargins(0, 0, 0, 0)
    g.setHorizontalSpacing(GAP)
    for c in range(4):
        g.setColumnStretch(c, 1)
        g.setColumnMinimumWidth(c, 1)
    col = 0
    for wid, span in items:
        if wid is not None:
            if isinstance(wid, (QPushButton, QAbstractSpinBox)):
                wid.setFixedHeight(BTN_H)
                wid.setMinimumWidth(1)
                wid.setMaximumWidth(16777215)
                wid.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            elif isinstance(wid, QLabel):
                wid.setMinimumWidth(1)
                wid.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            else:
                wid.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            g.addWidget(wid, 0, col, 1, span)
        col += span
    return w


def no_hfw(lab: QLabel, lines: int = 2) -> QLabel:
    """줄바꿈 글(QLabel wordWrap)이 창 최소 높이를 크게 키우지 않게: 높이-폭 계산을 끄고 줄 수만큼 높이를 잡음."""
    lab.setWordWrap(True)
    sp = lab.sizePolicy()
    sp.setHeightForWidth(False)
    lab.setSizePolicy(sp)
    lab.setMinimumHeight(lab.fontMetrics().lineSpacing() * lines + 2)
    lab.setMinimumWidth(10)
    return lab


def labeled(text, w, lw=76):
    row = QHBoxLayout()
    row.setSpacing(5)
    lab = QLabel(text)
    lab.setFixedWidth(lw)
    lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    row.addWidget(lab)
    if isinstance(w, QWidget):
        row.addWidget(w, 1)
    else:
        row.addLayout(w, 1)
    return row


class Grip(QWidget):
    """테두리 없는 창 맨 위 손잡이(점 10개). 누름은 창으로 넘어가 DragMove 가 옮김."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(14)
        self.setCursor(Qt.SizeAllCursor)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#6a6a6a"))
        cx, cy = self.width() / 2, self.height() / 2
        for i in range(-2, 3):
            for j in (-2, 2):
                p.drawEllipse(QRectF(cx + i * 7 - 1.5, cy + j - 1.5, 3, 3))
        p.end()


class Seg(QWidget):
    """[a|b|c] 하나만 켜짐(떨어진 버튼, 간격 5, 켜짐 = 파랑)."""
    changed = Signal(str)

    def __init__(self, opts, cur=None, height=30, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.btns: dict[str, QPushButton] = {}
        for k, t in opts:
            b = QPushButton(t)
            b.setCheckable(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.setFixedHeight(height)
            b.setMinimumWidth(36)                 # Fusion 글자 버튼 최소 폭(80) 대신 — 좁은 칸에 5개도 들어가게
            self.group.addButton(b)
            self.btns[k] = b
            lay.addWidget(b, 1)
            b.clicked.connect(lambda _=False, k=k: (self._grey(), self.changed.emit(k)))
        self.set_value(cur if cur in self.btns else next(iter(self.btns)))

    def value(self):
        return next((k for k, b in self.btns.items() if b.isChecked()), None)

    def set_value(self, k):
        if k in self.btns:
            self.btns[k].setChecked(True)
            self._grey()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.EnabledChange:
            self._grey()

    def _grey(self):
        """꺼짐(비활성)이면 켜진 버튼도 흐린 회청색·회색 글자로(ui_style 은 꺼져도 밝은 파랑으로 그려서, v0.1.6)."""
        on = self.isEnabled()
        for b in self.btns.values():
            want = "" if on or not b.isChecked() else ui_style.dim(ui_style.SEL_BLUE)
            if (b.property("tone_color") or "") != want:
                ui_style.tone(b, color=want)


class Multi(QWidget):
    """여러 개 켜기(최대 limit개, 순서 기억). 넘으면 refused 신호. cols 열 격자."""
    changed = Signal(list)
    refused = Signal()

    def __init__(self, opts, cur=(), limit=3, cols=4, height=30, min_one=True, parent=None):
        super().__init__(parent)
        g = QGridLayout(self)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(5)
        g.setVerticalSpacing(5)
        self.limit, self.min_one = limit, min_one
        self.order: list[str] = []
        self.btns: dict[str, QPushButton] = {}
        for i, (k, t) in enumerate(opts):
            b = QPushButton(t)
            b.setCheckable(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.setFixedHeight(height)
            b.setMinimumWidth(36)
            b.clicked.connect(lambda on, k=k: self._click(k, on))
            self.btns[k] = b
            g.addWidget(b, i // cols, i % cols)
        self.set_value(list(cur))

    def set_swap(self, on: bool):
        """꽉 찼을 때 다른 것을 누르면 거절하지 않고 가장 오래 켠 것과 바꿈(limit=1 이면 '하나만 고르기')."""
        self.swap = bool(on)

    def _click(self, k, on):
        if on:
            if len(self.order) >= self.limit and getattr(self, "swap", False):
                old = self.order.pop(0)
                self.btns[old].setChecked(False)
            if len(self.order) >= self.limit:
                self.btns[k].setChecked(False)
                self.refused.emit()
                return
            self.order.append(k)
        else:
            if self.min_one and len(self.order) <= 1:
                self.btns[k].setChecked(True)
                return
            if k in self.order:
                self.order.remove(k)
        self.changed.emit(list(self.order))

    def value(self) -> list:
        return list(self.order)

    def set_value(self, keys):
        self.order = [k for k in keys if k in self.btns][: self.limit]
        for k, b in self.btns.items():
            b.setChecked(k in self.order)

    def set_enabled_all(self, on: bool):
        for b in self.btns.values():
            b.setEnabled(on)


class StageRow(QFrame):
    """[✓] 이름  요약 …  ▸ — 체크 칸 = 켬/끔(fixed 면 늘 켬), 줄 나머지 = 펼침. (DTME StageRow 그대로)"""
    toggled = Signal(str, bool)
    clicked = Signal(str)

    def __init__(self, key: str, body: QWidget, fixed: bool = False, parent=None):
        super().__init__(parent)
        self.key, self.body, self.fixed = key, body, fixed
        self.on, self.open_ = True, False
        self.locked = False
        self.title = ""                       # 머리 줄 이름 바꾸기(예: 생성 → 후보정)
        self.summary = ""
        self.setObjectName("StageRow")
        self.setStyleSheet("#StageRow{background:#2a2a2a; border:1px solid #3a3a3a; border-radius:3px;}")
        self.head = QWidget(self)
        self.head.setFixedHeight(34)
        if not fixed:
            self.head.setCursor(Qt.PointingHandCursor)
        self.head.installEventFilter(self)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self.head)
        v.addWidget(body)
        self.open_ = True                          # DTME v0.17.2: 설정 칸 = 늘 펼침

    def set_on(self, on: bool):
        self.on = True if self.fixed else bool(on)
        self.body.setEnabled(self.on)
        self.head.update()

    def set_locked(self, locked: bool):
        """잠금 = 체크 칸 꺼짐 · 못 누름(설정 칸은 그대로 씀) — 예: 원곡이 없을 때 '분석'."""
        self.locked = bool(locked)
        if self.locked:
            self.on = False
            self.body.setEnabled(True)
        self.head.setCursor(Qt.ArrowCursor if self.locked or self.fixed else Qt.PointingHandCursor)
        self.head.update()

    def set_open(self, on: bool):
        """(늘 펼침 — 예전 이름만 남김)"""
        self.open_ = True
        self.body.setVisible(True)

    def set_summary(self, text: str):
        if text != self.summary:
            self.summary = text
            self.head.update()

    def eventFilter(self, obj, e):
        if obj is self.head:
            if e.type() == QEvent.MouseButtonPress and e.button() == Qt.LeftButton:
                if not self.fixed and not self.locked:   # 머리 줄 아무 데나 누름 = 켬/끔(끈 단계 = 설정 흐림)
                    self.set_on(not self.on)
                    self.toggled.emit(self.key, self.on)
                return True
            if e.type() == QEvent.Paint:
                self._paint_head()
                return True
        return False

    def _paint_head(self):
        p = QPainter(self.head)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.head.rect())
        col = color_of(self.key)
        act = self.on
        cb = QRectF(12, r.center().y() - 8, 16, 16)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(CHK_BG))
        p.drawRoundedRect(cb, 2, 2)
        if act:
            p.setPen(QPen(QColor("#ffffff" if not self.fixed else "#9a9a9a"), 1.8))
            p.drawLine(cb.left() + 4, cb.center().y(), cb.left() + 7, cb.bottom() - 4)
            p.drawLine(cb.left() + 7, cb.bottom() - 4, cb.right() - 4, cb.top() + 4)
        f = QFont(self.font())
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(col if act else "#6f6f6f"))
        p.drawText(QRectF(40, 0, 44, r.height()), Qt.AlignVCenter | Qt.AlignLeft, self.title or name_of(self.key))
        f.setBold(False)
        p.setFont(f)
        p.setPen(QColor(SUB if act else "#6f6f6f"))
        w = r.width() - 88 - 10
        p.drawText(QRectF(88, 0, w, r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                   p.fontMetrics().elidedText(self.summary if act else "끔", Qt.ElideRight, int(w)))
        p.end()


class StepBar(QWidget):
    """막대 하나: 켠 단계마다 그 단계 색으로 왼쪽부터 다시 차오름. 위 = 단계 이름(끝남 ✓). (DTME StepBar 그대로)"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.steps: list[str] = []
        self.k, self.v, self.text = 0, 0.0, ""
        self.done = False
        self.setFixedHeight(34)

    def set_steps(self, steps: list[str]):
        self.steps, self.k, self.v, self.text, self.done = list(steps), 0, 0.0, "", False
        self.update()

    def set(self, key: str, v: float, text: str = ""):
        if key in self.steps:
            self.k = max(self.k, self.steps.index(key))
        self.v, self.text = max(0.0, min(1.0, v)), text
        self.update()

    def finish(self, text: str = ""):
        self.k, self.v, self.text, self.done = max(0, len(self.steps) - 1), 1.0, text, True
        self.update()

    def frac(self) -> float:
        n = max(1, len(self.steps))
        return (self.k + self.v) / n

    def paintEvent(self, _e):
        if not self.steps:
            return
        p = QPainter(self)
        f = QFont(self.font())
        x = 0
        for i, key in enumerate(self.steps):
            fin = i < self.k or self.done
            f.setBold(i == self.k and not self.done)
            p.setFont(f)
            p.setPen(QColor(color_of(key) if i <= self.k else "#5a5a5a"))
            t = ("✓ " if fin else "") + name_of(key)
            w = p.fontMetrics().horizontalAdvance(t) + 18
            p.drawText(x, 0, w, 20, Qt.AlignLeft | Qt.AlignVCenter, t)
            x += w
        key = self.steps[self.k]
        if self.done:
            lab = self.text or "완료"
        else:
            lab = f"{self.k + 1}/{len(self.steps)} {name_of(key)}" + (f" · {self.text}" if self.text else f" {int(self.v * 100)}%")
        f.setBold(False)
        p.setFont(f)
        p.setPen(QColor(color_of(key)))
        p.drawText(x, 0, self.width() - x, 20, Qt.AlignRight | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(lab, Qt.ElideLeft, max(10, self.width() - x)))
        y, h, w = 28, 6, self.width()
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)

        def bar(width, col):
            if width > 0:
                p.setBrush(QColor(col))
                p.drawRoundedRect(0, y, width, h, 3, 3)
        bar(w, "#2a2a2a")
        if self.k > 0:
            bar(w, color_of(self.steps[self.k - 1]))
        bar(int(w * self.v), color_of(key))
        p.end()


class Busy(QLabel):
    """작은 글자 점 애니메이션(… 이 돎) — 오래 걸리는 단계에서 멈춘 게 아님을 보여 줌."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._base, self._n = "", 0
        self._t = QTimer(self)
        self._t.setInterval(450)
        self._t.timeout.connect(self._tick)

    def run(self, text: str):
        self._base = text
        if not self._t.isActive():
            self._t.start()
        self._tick()

    def halt(self, text: str = ""):
        self._t.stop()
        self.setText(text)

    def _tick(self):
        self._n = (self._n + 1) % 4
        self.setText(self._base + "." * self._n)
