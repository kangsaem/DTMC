"""시작 창(DTME 시작 창 틀 그대로): 제목 표시줄·테두리 없는 400×560, 위 = 정사각형 그림(assets/start.png)
+ 왼쪽 위 'DTMC'·버전 + 오른쪽 위 ✕(종료) + 오른쪽 아래 세로 글자, 아래(#262626) = [AUTO] 160×48. 빈 곳을 끌어 옮김.
[AUTO] = 빈 작업 창 · 원곡을 끌어다 놓기 = 그 파일이 들어간 작업 창. 작업 창 [취소] = 이 창으로."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

from . import __version__, paths, ui_style

IMG = 400
W, H = IMG, IMG + 160
SIDE_TEXT = "Desktop Music Composer"
GRAD_W, GRAD_H = IMG // 2, 112                 # 제목 뒤 그라데이션(왼쪽 절반 · 높이 112)


class SideText(QWidget):
    """그림 오른쪽 아래 세로 글자(아래 → 위). 아주 가는 글씨, 흰색 알파 225, 자간 1.5px(DTME 와 같음)."""

    def __init__(self, text, parent):
        super().__init__(parent)
        self.text = text
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        f = QFont("Segoe UI")
        f.setPixelSize(14)
        f.setWeight(QFont.ExtraLight)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 1.5)
        self.setFont(f)
        fm = self.fontMetrics()
        self.setFixedSize(fm.height() + 2, fm.horizontalAdvance(text) + 4)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        p.setPen(QColor(255, 255, 255, 225))
        p.translate(0, self.height())
        p.rotate(-90)
        p.drawText(0, 0, self.height(), self.width(), Qt.AlignLeft | Qt.AlignVCenter, self.text)
        p.end()


class StartWindow(QWidget):
    def __init__(self, open_work):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint)
        self.open_work = open_work            # open_work(파일 목록) → 작업 창
        self.setWindowTitle(f"DTMC {__version__}")
        self.setObjectName("Start")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAcceptDrops(True)
        self.setFixedSize(W, H)
        self._drag = None
        self._style(False)
        self.img = QLabel(self)
        self.img.setFixedSize(IMG, IMG)
        self.img.setAlignment(Qt.AlignCenter)
        pm = QPixmap(str(paths.asset("start.png")))
        if pm.isNull():
            self.img.setStyleSheet("background:#3a3a3a;")
        else:
            dpr = self.devicePixelRatioF() or 1.0
            pm = pm.scaled(round(IMG * dpr), round(IMG * dpr), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            self.img.setPixmap(pm)
        if not pm.isNull():                    # 제목 뒤 어두운 그라데이션(밝은 그림에서도 흰 글자가 보이게, MOSCO v0.7.92 방식)
            grad = QWidget(self.img)
            grad.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            grad.setAttribute(Qt.WA_StyledBackground, True)
            grad.setGeometry(0, 0, GRAD_W, GRAD_H)
            grad.setStyleSheet("background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 rgba(20,30,45,235),"
                               "stop:0.7 rgba(20,30,45,160),stop:1 rgba(20,30,45,0))")
        head = QWidget(self.img)
        head.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        hl = QVBoxLayout(head)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(0)
        self.title = QLabel("DTMC", head)
        tf = QFont(self.title.font())
        tf.setPixelSize(42)
        tf.setBold(True)
        tf.setLetterSpacing(QFont.AbsoluteSpacing, 3)
        self.title.setFont(tf)
        self.title.setStyleSheet("color:#ffffff; background:transparent;")
        self.ver = QLabel(f"v{__version__}", head)
        vf = QFont(self.ver.font())
        vf.setPixelSize(13)
        self.ver.setFont(vf)
        self.ver.setStyleSheet("color:#e6e6e6; padding-left:2px; background:transparent;")
        hl.addWidget(self.title)
        hl.addWidget(self.ver)
        head.adjustSize()
        head.move(18, 12)
        self.side = SideText(SIDE_TEXT, self.img)
        self.side.move(IMG - 14 - self.side.width(), IMG - 14 - self.side.height())
        self.b_close = QPushButton("✕", self.img)
        self.b_close.setToolTip("종료")
        self.b_close.setCursor(Qt.PointingHandCursor)
        self.b_close.setFocusPolicy(Qt.NoFocus)
        self.b_close.setFixedSize(26, 26)
        self.b_close.setStyleSheet("QPushButton{color:#ffffff; background:rgba(0,0,0,90); border:none; border-radius:13px;"
                                   " font-size:14px; padding:0;} QPushButton:hover{background:#c0392b;}")
        self.b_close.move(IMG - 26 - 10, 10)
        self.b_close.clicked.connect(self.close)
        self.b_auto = QPushButton("AUTO")
        self.b_auto.setFixedSize(160, 48)
        self.b_auto.setFocusPolicy(Qt.NoFocus)
        ui_style.tone(self.b_auto, "red")
        f = QFont(self.b_auto.font())
        f.setPointSizeF(f.pointSizeF() * 1.3)
        f.setBold(True)
        self.b_auto.setFont(f)
        self.b_auto.clicked.connect(lambda: self.open_work([]))
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self.img)
        v.addStretch(1)
        v.addWidget(self.b_auto, 0, Qt.AlignHCenter)
        v.addStretch(1)

    def _style(self, hot):
        self.setStyleSheet("#Start{background:#262626; border:none;}" if not hot else
                           "#Start{background:#262a30; border:2px dashed #4a8fe0;}")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag = None
        super().mouseReleaseEvent(e)

    def center(self):
        scr = self.screen().availableGeometry()
        self.move(scr.center().x() - self.width() // 2, scr.center().y() - self.height() // 2)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._style(True)

    def dragLeaveEvent(self, e):
        self._style(False)
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        self._style(False)
        self.open_work([u.toLocalFile() for u in e.mimeData().urls()])

    def closeEvent(self, e):
        e.accept()
        QApplication.instance().quit()
