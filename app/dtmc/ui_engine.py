"""생성 엔진 설치 창(처음 한 번): 설치 위치·여유 공간·이 컴퓨터 실행 모드 → [설치 시작] → 단계 막대 + 진행 글(로그 끝부분).
테두리 없는 어두운 창(DTME 확인 창 모양). 설치는 백그라운드 스레드(InstallJob)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout, QWidget)

from . import engine as EG
from . import hw, log, paths, settings, ui_style
from .ui_widgets import WARN, DragMove, Grip, StepBar, main_button, small_button

L = log.log

class InstallJob(QThread):
    step = Signal(str, int, int)
    line = Signal(str)
    finished_ok = Signal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cancel = False

    def run(self):
        try:
            EG.install(lambda k, i, n: self.step.emit(k, i, n), self.line.emit, lambda: self.cancel)
            self.finished_ok.emit(True, "설치 완료")
        except EG.Cancelled:
            self.finished_ok.emit(False, "설치를 중지했습니다. 다시 시작하면 받던 곳부터 이어서 받습니다.")
        except Exception as e:  # noqa: BLE001
            L.exception("엔진 설치 실패")
            self.finished_ok.emit(False, str(e))


class EngineDialog(DragMove, QDialog):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.setModal(True)
        self.setObjectName("EngineDlg")
        self.setStyleSheet("#EngineDlg{background:#262626; border:1px solid #555; border-radius:6px;}"
                           "QLabel{color:#e6e6e6;}")
        self.job: InstallJob | None = None
        self.ok = False
        pr = hw.profile()
        title = QLabel("생성 엔진 설치")
        f = QFont(title.font())
        f.setPixelSize(17)
        f.setBold(True)
        title.setFont(f)
        intro = QLabel("처음 한 번만 설치합니다. Python · PyTorch · ACE-Step 1.5 · 음악 모델을 인터넷에서 받습니다.\n"
                       "받는 양이 많아(수 GB~수십 GB) 인터넷 속도에 따라 수십 분 이상 걸릴 수 있습니다.\n"
                       "중간에 멈춰도 다시 시작하면 이어서 받습니다.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#c4cad1;")
        self.path_lab = QLabel("")
        self.path_lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.b_path = small_button("변경", 56)
        self.b_path.clicked.connect(self.pick_dir)
        pr_row = QHBoxLayout()
        pr_row.addWidget(QLabel("설치 위치"))
        pr_row.addSpacing(8)
        pr_row.addWidget(self.path_lab, 1)
        pr_row.addWidget(self.b_path)
        self.free_lab = QLabel("")
        self.mode_lab = QLabel(f"이 컴퓨터: {pr.label} → {'GPU 고품질 모드' if pr.mode == 'gpu' else 'CPU 경량 모드(느림)'}")
        self.warn_lab = QLabel(pr.warn)
        self.warn_lab.setWordWrap(True)
        self.warn_lab.setStyleSheet(f"color:{WARN};")
        self.warn_lab.setVisible(bool(pr.warn))
        info = QVBoxLayout()
        info.setSpacing(6)
        info.addLayout(pr_row)
        info.addWidget(self.free_lab)
        info.addWidget(self.mode_lab)
        info.addWidget(self.warn_lab)
        self.info_w = QWidget()
        self.info_w.setLayout(info)
        self.bar = StepBar()
        self.bar.set_steps(["engine"])
        self.bar.setVisible(False)
        self.stage_lab = QLabel("")
        self.stage_lab.setStyleSheet("color:#c4cad1;")
        self.logv = QPlainTextEdit()
        self.logv.setReadOnly(True)
        self.logv.setMaximumBlockCount(400)
        mf = QFont("Consolas")
        mf.setStyleHint(QFont.Monospace)
        mf.setPixelSize(11)
        self.logv.setFont(mf)
        self.logv.setStyleSheet("QPlainTextEdit{background:#1a1a1a; color:#b8b8b8; border:1px solid #333;}")
        self.logv.setVisible(False)
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        self.b_cancel = main_button("취소")
        self.b_cancel.clicked.connect(self.on_cancel)
        self.b_go = main_button("설치 시작", "red", 100)
        self.b_go.clicked.connect(self.start)
        bot = QHBoxLayout()
        bot.addWidget(self.msg, 1)
        bot.addWidget(self.b_cancel)
        bot.addWidget(self.b_go)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 8, 22, 16)
        lay.setSpacing(12)
        lay.addWidget(Grip())
        lay.addWidget(title)
        lay.addWidget(intro)
        lay.addWidget(self.info_w)
        lay.addWidget(self.bar)
        lay.addWidget(self.stage_lab)
        lay.addWidget(self.logv, 1)
        lay.addLayout(bot)
        self.resize(640, 420)
        self._refresh()

    def _refresh(self):
        d = paths.engine_dir()
        self.path_lab.setText(str(d))
        free = hw.disk_free_gb(d)
        ok_free = free >= EG.NEED_GB
        self.free_lab.setText(f"여유 공간 {free:g}GB (필요 {EG.NEED_GB}GB 이상)")
        self.free_lab.setStyleSheet("" if ok_free else f"color:{WARN};")
        plain = paths.plain(d)
        self.path_lab.setStyleSheet("" if plain else f"color:{WARN};")
        self.b_go.setEnabled(ok_free and plain)
        if not plain:
            self.msg.setText("설치 위치에 한글·띄어쓰기가 없어야 합니다. [변경]으로 다른 폴더를 고르세요.")
        elif not ok_free:
            self.msg.setText("여유 공간이 부족합니다. 다른 드라이브를 고르세요.")
        else:
            self.msg.setText("")

    def pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "엔진 설치 위치", str(paths.engine_dir().parent))
        if not d:
            return
        p = Path(d)
        if p.name.lower() != "engine" and not (p / "installed.json").exists():
            p = p / "DTMC_engine"
        settings.put("engine_dir", str(p))
        self._refresh()

    def start(self):
        self.info_w.setEnabled(False)
        self.b_go.setVisible(False)
        self.b_cancel.setText("중지")
        self.bar.setVisible(True)
        self.logv.setVisible(True)
        self.bar.set("engine", 0.0, "시작")
        self.msg.setText("")
        self.job = InstallJob(self)
        self.job.step.connect(self.on_step)
        self.job.line.connect(self.on_line)
        self.job.finished_ok.connect(self.on_done)
        self.job.start()

    def on_step(self, key, i, n):
        name = dict(EG.STEPS).get(key, key)
        self.bar.set("engine", (i - 1) / n, f"{i}/{n} {name}")
        self.stage_lab.setText(f"{i}/{n} {name}")

    def on_line(self, t):
        self.logv.appendPlainText(t)

    def on_done(self, ok, msg):
        self.ok = ok
        self.job = None
        self.b_cancel.setText("닫기")
        if ok:
            self.bar.finish("설치 완료")
            self.msg.setText("설치가 끝났습니다.")
            self.msg.setStyleSheet("color:#3fb8a0;")
            self.b_go.setVisible(True)
            self.b_go.setText("확인")
            ui_style.tone(self.b_go, "blue")
            self.b_go.clicked.disconnect()
            self.b_go.clicked.connect(self.accept)
            self.b_go.setEnabled(True)
        else:
            self.b_cancel.setEnabled(True)
            self.msg.setText(msg)
            self.msg.setStyleSheet(f"color:{WARN};")
            self.b_go.setVisible(True)
            self.b_go.setText("다시 시도")
            self.b_go.setEnabled(True)
            self.info_w.setEnabled(True)

    def on_cancel(self):
        if self.job is not None:
            self.job.cancel = True
            self.b_cancel.setEnabled(False)
            self.msg.setText("중지하는 중…")
            return
        self.accept() if self.ok else self.reject()

    def closeEvent(self, e):
        if self.job is not None:
            e.ignore()
            return
        super().closeEvent(e)
