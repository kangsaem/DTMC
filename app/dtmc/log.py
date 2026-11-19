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
        # v0.1.18: 이번 실행은 crash_last.log 에 쓰고, 다음 시작 때 무해한 윈도우 경고를 걸러 crash.log 에 옮김
        _tidy_crash(d)
        _crash = open(d / "crash_last.log", "w", encoding="utf-8")      # noqa: SIM115 — 끝까지 열어 둠
        faulthandler.enable(_crash)
    except Exception:  # noqa: BLE001
        pass

    def hook(t, v, tb):
        log.error("처리 안 된 오류", exc_info=(t, v, tb))
    sys.excepthook = hook
    threading.excepthook = lambda a: log.error("스레드 오류", exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
    log.info("DTMC %s 시작 · Python %s · %s", __version__, sys.version.split()[0], sys.platform)


# 프로그램은 계속 도는 윈도우 경고(faulthandler 가 '치명적'처럼 적지만 아님) — 0x8001010d = COM 통신 경고(11/19 실기)
HARMLESS = ("0x8001010d", "0x8001010e", "0x40010006", "0x406d1388")


def filter_crash(text: str) -> str:
    """faulthandler 기록에서 무해한 윈도우 경고 덩어리를 뺌. 덩어리 = 'Windows fatal exception' · 'Fatal Python error' 줄부터."""
    out, keep = [], True
    for line in text.splitlines(keepends=True):
        if line.startswith(("Windows fatal exception", "Fatal Python error")):
            keep = not any(c in line for c in HARMLESS)
        if keep:
            out.append(line)
    return "".join(out)


def _tidy_crash(d) -> None:
    """지난 실행 기록(crash_last.log)과 예전 crash.log 에서 무해한 경고를 빼고, 남은 진짜 오류만 crash.log 에."""
    import time as _t
    main, last = d / "crash.log", d / "crash_last.log"
    old = main.read_text(encoding="utf-8", errors="replace") if main.exists() else ""
    new = last.read_text(encoding="utf-8", errors="replace") if last.exists() else ""
    kept = filter_crash(new).strip()
    body = filter_crash(old)
    if kept:
        body += f"\n--- {_t.strftime('%Y-%m-%d %H:%M:%S', _t.localtime(last.stat().st_mtime))} 지난 실행 ---\n{kept}\n"
    if body != old:
        main.write_text(body, encoding="utf-8")
    if last.exists():
        last.unlink()


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
