"""시작 창(DTME 시작 창 틀 그대로): 제목 표시줄·테두리 없는 400×560, 위 = 정사각형 영상(assets/start.mp4, v0.1.15:
처음엔 첫 장면(start_poster.jpg) → [AUTO] 를 누르면 소리 없이 재생 → 끝나면 작업 창(v0.1.16).
재생 중 [AUTO] = [건너뛰기] · 그림 클릭 = 건너뛰기 · 원곡을 끌어다 놓으면 바로. 영상을 못 쓰면 start.png · 바로 작업 창)
+ 왼쪽 위 'DTMC'·버전 + 오른쪽 위 ✕(종료) + 오른쪽 아래 세로 글자, 아래(#262626) = [AUTO] 160×48. 빈 곳을 끌어 옮김.
[AUTO] = 빈 작업 창 · 원곡을 끌어다 놓기 = 그 파일이 들어간 작업 창. 작업 창 [취소] = 이 창으로."""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

from . import __version__, log, paths, ui_style

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
        self._press = None
        self._pending = None                  # 영상이 끝나면 열 작업(파일 목록)
        self._style(False)
        self.img = QLabel(self)
        self.img.setFixedSize(IMG, IMG)
        self.img.setAlignment(Qt.AlignCenter)
        self.player = self.sink = None
        video = paths.asset("start.mp4")
        poster = paths.asset("start_poster.jpg")
        pm = self._show(QPixmap(str(poster))) if video.is_file() else QPixmap()
        if pm.isNull() or not self._play(video):
            pm = self._show(QPixmap(str(paths.asset("start.png"))))
        if pm.isNull():
            self.img.setStyleSheet("background:#3a3a3a;")
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
        self.b_auto.clicked.connect(self._auto)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self.img)
        v.addStretch(1)
        v.addWidget(self.b_auto, 0, Qt.AlignHCenter)
        v.addStretch(1)

    def _show(self, pm: QPixmap) -> QPixmap:
        """그림(또는 영상 한 장면)을 400×400 칸에 꽉 차게."""
        if pm.isNull():
            return pm
        dpr = self.devicePixelRatioF() or 1.0
        pm = pm.scaled(round(IMG * dpr), round(IMG * dpr), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        pm.setDevicePixelRatio(dpr)
        self.img.setPixmap(pm)
        return pm

    def _play(self, video) -> bool:
        """영상 준비(재생은 [AUTO] 때). 장면마다 그림 칸에 그림 → 겹친 제목·그라데이션·✕ 가 그대로 위에 보임.
        못 쓰면 False(→ 그림)."""
        try:
            from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
            self.player = QMediaPlayer(self)                 # 소리 출력(QAudioOutput)을 안 붙임 = 소리 없음
            self.sink = QVideoSink(self)
            self.player.setVideoSink(self.sink)
            self.sink.videoFrameChanged.connect(self._frame)
            self.player.errorOccurred.connect(self._video_error)
            self.player.mediaStatusChanged.connect(self._status)
            self.player.setSource(QUrl.fromLocalFile(str(video)))
            return True
        except Exception as e:  # noqa: BLE001
            log.log.warning("시작 영상 못 씀(그림으로): %s", e)
            self.player = self.sink = None
            return False

    def _auto(self):
        """[AUTO]: 영상을 재생하고 끝나면 작업 창. 재생 중이면 [건너뛰기]."""
        if self._pending is not None:
            return self._go()
        if self.player is None:
            return self.open_work([])
        self._pending = []
        self.b_auto.setText("건너뛰기")
        self.player.setPosition(0)
        self.player.play()

    def _status(self, st):
        from PySide6.QtMultimedia import QMediaPlayer
        if st == QMediaPlayer.EndOfMedia and self._pending is not None:
            self._go()

    def _video_error(self, _e, msg=""):
        log.log.warning("시작 영상 재생 실패: %s", msg)
        if self._pending is not None:
            self._go()

    def _go(self):
        """영상 재생을 끝내고 작업 창으로(한 번만)."""
        files, self._pending = self._pending, None
        if files is None:
            return
        if self.player is not None:
            self.player.pause()
        self.b_auto.setText("AUTO")
        self.open_work(files)

    def _frame(self, f):
        try:
            im = f.toImage()
        except Exception:  # noqa: BLE001
            return
        if not im.isNull():
            self._show(QPixmap.fromImage(im))

    def _style(self, hot):
        self.setStyleSheet("#Start{background:#262626; border:none;}" if not hot else
                           "#Start{background:#262a30; border:2px dashed #4a8fe0;}")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._press = e.globalPosition().toPoint()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag = None
        p0, self._press = self._press, None
        if (self._pending is not None and p0 is not None and e.position().y() < IMG
                and (e.globalPosition().toPoint() - p0).manhattanLength() < 5):   # 재생 중 그림 클릭 = 건너뛰기
            self._go()
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
        files = [u.toLocalFile() for u in e.mimeData().urls()]
        if self._pending is not None:                         # 재생 중이면 멈추고 바로
            self._pending = None
            if self.player is not None:
                self.player.pause()
            self.b_auto.setText("AUTO")
        self.open_work(files)

    def hideEvent(self, e):
        if self.player is not None:                           # 작업 창으로 넘어가면 멈춤(마지막 장면 유지)
            self.player.pause()
        super().hideEvent(e)

    def closeEvent(self, e):
        if self.player is not None:
            self.player.stop()
        e.accept()
        QApplication.instance().quit()
