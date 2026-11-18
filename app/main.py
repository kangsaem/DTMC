"""DTMC 실행 진입점. 음원 파일을 인자로 주거나(끌어다 실행) 시작 창에 끌어다 놓으면 바로 작업 창에 들어감."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QColor, QIcon, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def dark(app: QApplication):
    """DTME·MOSCO 와 같은 어두운 팔레트 + 버튼 입체감(DTME ui_style.BevelStyle)."""
    from dtmc import ui_style
    app.setStyle(ui_style.BevelStyle())
    p = QPalette()
    c = {QPalette.Window: "#232323", QPalette.WindowText: "#e6e6e6", QPalette.Base: "#1a1a1a",
         QPalette.AlternateBase: "#262626", QPalette.Text: "#e6e6e6", QPalette.Button: "#2e2e2e",
         QPalette.ButtonText: "#e6e6e6", QPalette.Highlight: "#4a6fa5", QPalette.HighlightedText: "#ffffff",
         QPalette.ToolTipBase: "#333", QPalette.ToolTipText: "#eee", QPalette.PlaceholderText: "#777"}
    for role, col in c.items():
        p.setColor(role, QColor(col))
    app.setPalette(p)


class App:
    """시작 창 ↔ 작업 창 오가기 + 엔진(하나) 관리."""

    def __init__(self, app: QApplication):
        from dtmc import engine
        from dtmc.ui_main import WorkWindow
        from dtmc.ui_start import StartWindow
        self.app = app
        self.eng = engine.Engine()
        self.start = StartWindow(self.open_work)
        self.work = WorkWindow(self.eng)

    def open_work(self, files):
        from dtmc.ui_widgets import bring_front
        w = self.work
        if not w.isVisible():
            g = self.start.geometry() if self.start.isVisible() else self.app.primaryScreen().availableGeometry()
            w.move(g.center().x() - w.width() // 2, g.center().y() - w.height() // 2)
        self.start.hide()
        w.show()
        if files:
            w.add_paths(files)
        bring_front(w)

    def front(self):
        """두 번째 실행이 왔을 때: 지금 보이는 창을 맨 앞으로."""
        from dtmc.ui_widgets import bring_front
        w = self.work if self.work.isVisible() else self.start
        if not w.isVisible():
            w.show()
        bring_front(w)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("DTMC")
    selftest = bool(os.environ.get("DTMC_SELFTEST"))
    from dtmc import single
    if not single.acquire("DTMC", skip=selftest):
        _hard_exit(0)
    from dtmc import log, paths, procs
    log.setup()
    procs.join_job()                          # DTMC 가 어떻게 끝나든 엔진·uv·ffmpeg 가 같이 꺼지게
    _prepare_engine()
    ico = paths.asset("dtmc.ico")
    if ico.exists():
        app.setWindowIcon(QIcon(str(ico)))
    dark(app)
    a = App(app)
    single.serve("DTMC", a.front, app)
    app.setQuitOnLastWindowClosed(False)
    app.aboutToQuit.connect(lambda: _cleanup(a))
    args = [x for x in sys.argv[1:] if Path(x).exists()]
    if args:
        a.open_work(args)
    else:
        a.start.center()
        a.start.show()
        from dtmc.ui_widgets import bring_front
        QTimer.singleShot(0, lambda: bring_front(a.start))
    if selftest:
        QTimer.singleShot(300, lambda: _selftest(a))
    _watch_windows(app)
    code = app.exec()
    _cleanup(a)
    _hard_exit(code if isinstance(code, int) else 0)


def _prepare_engine():
    """시작 때: 이전 실행이 남긴 엔진 프로세스 정리 → 엔진이 버전 폴더 안이면 \\DTMC_engine 으로 옮김."""
    from dtmc import engine, log, procs
    try:
        n = procs.cleanup_leftovers()
        if n:
            log.log.info("이전 실행이 남긴 프로세스 %d개 정리", n)
        engine.relocate()
    except Exception:  # noqa: BLE001
        log.log.exception("엔진 준비(정리·옮기기) 실패")


def _selftest(a):
    """빌드 점검(DTMC_SELFTEST=1): 창 2개 · 엔진 소스·uv·ffmpeg 확인 · 시험 음원 분석 → 종료 코드 0/3."""
    from dtmc import analyze, engine, ff, log, paths
    ok = True
    try:
        assert (paths.engine_src() / "pyproject.toml").exists(), "engine_src 없음"
        assert (paths.engine_src() / "uv.lock").exists(), "uv.lock 없음"
        paths.uv()
        paths.ffmpeg()
        p = ff.make_test_tone(paths.user_dir() / "selftest.wav", 12, 120)
        an = analyze.analyze_file(p)
        assert 110 < an.bpm < 130, an.bpm
        out = ff.convert(p, paths.user_dir() / "selftest_out", "mp3")
        assert out.exists()
        a.open_work([str(p)])
        log.log.info("SELFTEST ok bpm=%s key=%s engine_installed=%s", an.bpm, an.key_scale, engine.installed())
    except Exception:  # noqa: BLE001
        log.log.exception("SELFTEST 실패")
        ok = False
    QTimer.singleShot(1500, lambda: a.app.exit(0 if ok else 3))


def _watch_windows(app):
    """안전장치(MOSCO v0.7.71): 보이는 창이 6초쯤 없으면 종료(창 없이 프로세스·엔진이 남지 않게)."""
    state = {"n": 0}

    def tick():
        vis = any(x.isVisible() for x in app.topLevelWidgets())
        state["n"] = 0 if vis else state["n"] + 1
        if state["n"] >= 4:
            app.quit()
    t = QTimer(app)
    t.setInterval(1500)
    t.timeout.connect(tick)
    t.start()
    app._dtmc_watch = t


_cleaned = False


def _cleanup(a):
    global _cleaned
    if _cleaned:
        return
    _cleaned = True
    try:
        if a.work.job is not None:
            a.work.job.cancel = True
        a.eng.stop()
    except Exception:  # noqa: BLE001
        pass
    try:
        from dtmc import engine, ff, paths, procs
        ff.kill_all()
        procs.kill_under([paths.engine_dir()])          # 엔진 Python 이 띄운 Python 까지(손자 프로세스)
        engine.kill_tree(a.eng.proc)
    except Exception:  # noqa: BLE001
        pass


def _hard_exit(code):
    """MOSCO 와 같음: 잠금 해제 후 TerminateProcess 로 즉시 종료(무거운 DLL 해제 중 교착 방지)."""
    try:
        from dtmc import single
        single.release()
    except Exception:  # noqa: BLE001
        pass
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:  # noqa: BLE001
        pass
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            k = ctypes.windll.kernel32
            k.GetCurrentProcess.restype = wintypes.HANDLE
            k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
            k.TerminateProcess.restype = wintypes.BOOL
            k.TerminateProcess(k.GetCurrentProcess(), code & 0xFFFFFFFF)
        except Exception:  # noqa: BLE001
            pass
    os._exit(code)


if __name__ == "__main__":
    main()
