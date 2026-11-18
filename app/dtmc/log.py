"""로그: 내 문서\\DTMC\\logs\\dtmc.log(프로그램) · engine.log(생성 엔진 출력) · crash.log(갑자기 죽을 때).
오류가 나면 이 폴더의 파일을 공유하면 원인을 찾을 수 있다."""
from __future__ import annotations

import faulthandler
import logging
import logging.handlers
import sys
import threading

log = logging.getLogger("dtmc")
_crash = None


def setup() -> None:
    global _crash
    if log.handlers:
        return
    from . import __version__, paths
    d = paths.log_dir()
    h = logging.handlers.RotatingFileHandler(d / "dtmc.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)
    if sys.stderr is not None:
        s = logging.StreamHandler(sys.stderr)
        s.setLevel(logging.WARNING)
        log.addHandler(s)
    try:
        _crash = open(d / "crash.log", "a", encoding="utf-8")      # noqa: SIM115 — 끝까지 열어 둠
        faulthandler.enable(_crash)
    except Exception:  # noqa: BLE001
        pass

    def hook(t, v, tb):
        log.error("처리 안 된 오류", exc_info=(t, v, tb))
    sys.excepthook = hook
    threading.excepthook = lambda a: log.error("스레드 오류", exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
    log.info("DTMC %s 시작 · Python %s · %s", __version__, sys.version.split()[0], sys.platform)


_engine_log = None
_elock = threading.Lock()


def engine_line(text: str) -> None:
    """생성 엔진(설치·서버) 출력 한 줄을 engine.log 에."""
    global _engine_log
    with _elock:
        try:
            if _engine_log is None:
                from . import paths
                p = paths.log_dir() / "engine.log"
                if p.exists() and p.stat().st_size > 20_000_000:
                    p.replace(p.with_suffix(".old.log"))
                _engine_log = open(p, "a", encoding="utf-8")        # noqa: SIM115
            _engine_log.write(text.rstrip("\n") + "\n")
            _engine_log.flush()
        except Exception:  # noqa: BLE001
            pass
