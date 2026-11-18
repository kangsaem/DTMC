"""실행 위치·외부 도구(ffmpeg·uv)·생성 엔진·사용자 폴더. 설치 없이 폴더째 실행(MOSCO paths 방식).

- 프로그램 폴더(app_dir): DTMC.exe · bin(ffmpeg·ffprobe·uv) · assets · engine_src(ACE-Step 소스, 읽기 전용)
- 엔진 폴더(engine_dir): 첫 실행 때 설치(Python·PyTorch·ACE-Step·모델, 수십 GB). 기본 = 프로그램 폴더\\engine.
  한글·띄어쓰기가 든 경로는 일부 라이브러리가 못 읽으므로 그때는 같은 드라이브의 \\DTMC_engine.
- 사용자 폴더(user_dir): 내 문서\\DTMC — 설정·로그·작업 기록·결과(기본 저장 폴더). 새 버전을 다른 폴더에 풀어도 그대로.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def bin_dir() -> Path:
    return app_dir() / "bin"


def asset(name: str) -> Path:
    return app_dir() / "assets" / name


def engine_src() -> Path:
    """빌드에 같이 넣은 ACE-Step 소스(고정 버전). 개발 실행 = 저장소 engine_src(없으면 설치 불가)."""
    return app_dir() / "engine_src" / "ACE-Step-1.5"


def plain(p) -> bool:
    """엔진을 둘 수 있는 경로인지: 영문·숫자 등 ASCII 만, 띄어쓰기 없음."""
    s = str(p)
    return s.isascii() and " " not in s


def _has_engine(d: Path) -> bool:
    return (d / "installed.json").exists() or (d / "ACE-Step-1.5" / ".venv").is_dir()


def engine_candidates() -> list[Path]:
    """이미 설치된 엔진을 찾을 곳: 고정 위치(C:\\DTMC_engine · 프로그램 드라이브\\DTMC_engine) ·
    이 프로그램 폴더\\engine · 옆 DTMC 버전 폴더\\engine — v0.1.2 이하는 프로그램 폴더 안에 설치했음."""
    out = [Path("C:\\DTMC_engine"), Path(app_dir().anchor or "C:\\") / "DTMC_engine", app_dir() / "engine"]
    try:
        for sib in sorted(app_dir().parent.iterdir()):
            if sib.is_dir() and sib != app_dir() and (sib / "DTMC.exe").exists():   # 다른 DTMC 버전 폴더만
                out.append(sib / "engine")
    except Exception:  # noqa: BLE001
        pass
    seen, uniq = set(), []
    for p in out:
        k = str(p).lower()
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def _engine_default() -> Path:
    """새로 설치할 곳 = 버전과 무관한 고정 위치. C: 여유가 부족하면 프로그램이 있는 드라이브\\DTMC_engine."""
    if os.name != "nt":
        return app_dir() / "engine"
    c = Path("C:\\DTMC_engine")
    try:
        from . import hw
        need = 30
        if hw.disk_free_gb(c) >= need:
            return c
        alt = Path(app_dir().anchor or "C:\\") / "DTMC_engine"
        if hw.disk_free_gb(alt) >= need:
            return alt
    except Exception:  # noqa: BLE001
        pass
    return c


_ENGINE_DIR = None


def engine_dir() -> Path:
    """환경 변수 DTMC_ENGINE_DIR > 설정(engine_dir, 모든 버전 공용) > 이미 설치된 곳(찾으면 설정에 기억) > 고정 기본 위치."""
    global _ENGINE_DIR
    env = os.environ.get("DTMC_ENGINE_DIR")
    if env:
        return Path(env)
    if _ENGINE_DIR is not None:
        return _ENGINE_DIR
    from . import settings
    s = settings.get("engine_dir", "")
    if s:
        _ENGINE_DIR = Path(s)
        return _ENGINE_DIR
    for d in engine_candidates():
        if _has_engine(d):
            settings.put("engine_dir", str(d))
            _ENGINE_DIR = d
            return d
    _ENGINE_DIR = _engine_default()
    return _ENGINE_DIR


def inside_version_folder(d: Path) -> bool:
    """엔진이 어떤 DTMC 버전 폴더 안(…\\DTMC_0.1.1\\engine)에 있는지 — 그러면 그 버전 폴더를 못 지움."""
    return d.name.lower() == "engine" and ((d.parent / "DTMC.exe").exists() or (d.parent / "_internal").is_dir())


def set_engine_dir(p) -> None:
    global _ENGINE_DIR
    from . import settings
    _ENGINE_DIR = Path(p)
    settings.put("engine_dir", str(p))


_USER_DIR = None


def _documents() -> Path:
    """윈도우 '내 문서' 실제 위치(OneDrive 로 옮겨진 경우 포함). 실패하면 홈\\Documents. (MOSCO 와 같음)"""
    if os.name == "nt":
        try:
            import ctypes
            import uuid
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]
            g = GUID()
            ctypes.memmove(ctypes.byref(g), uuid.UUID("FDD39AD0-238F-46AF-ADB4-6C85480369C7").bytes_le, 16)
            out = ctypes.c_wchar_p()
            sh = ctypes.WinDLL("shell32")
            sh.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE,
                                                ctypes.POINTER(ctypes.c_wchar_p)]
            if sh.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) == 0 and out.value:
                p = Path(out.value)
                ctypes.WinDLL("ole32").CoTaskMemFree(ctypes.cast(out, ctypes.c_void_p))
                return p
        except Exception:  # noqa: BLE001
            pass
    return Path.home() / "Documents"


def user_dir() -> Path:
    """내 문서\\DTMC(배포 exe). 개발 실행 = 저장소\\_user. 환경 변수 DTMC_USER_DIR 로 바꿀 수 있음."""
    global _USER_DIR
    if _USER_DIR is not None:
        return _USER_DIR
    env = os.environ.get("DTMC_USER_DIR")
    if env:
        d = Path(env)
    elif getattr(sys, "frozen", False):
        d = _documents() / "DTMC"
    else:
        d = app_dir() / "_user"
    try:
        d.mkdir(parents=True, exist_ok=True)
        t = d / ".write_test"
        t.write_text("ok", encoding="utf-8")
        t.unlink()
    except Exception:  # noqa: BLE001
        d = app_dir() / "_user"
        d.mkdir(parents=True, exist_ok=True)
    _USER_DIR = d
    return d


def _sub(name: str) -> Path:
    d = user_dir() / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_dir() -> Path:
    return _sub("logs")


def history_dir() -> Path:
    return _sub("history")


def default_out_dir() -> Path:
    return _sub("결과")


def settings_path() -> Path:
    return user_dir() / "settings.json"


def tool(name: str) -> str:
    exe = name + (".exe" if os.name == "nt" else "")
    local = bin_dir() / exe
    if local.exists():
        return str(local)
    found = shutil.which(name)
    if found:
        return found
    raise FileNotFoundError(f"{exe} 를 찾을 수 없습니다. 프로그램 폴더의 bin 에 있어야 합니다.")


def ffmpeg() -> str:
    return tool("ffmpeg")


def ffprobe() -> str:
    return tool("ffprobe")


def uv() -> str:
    return tool("uv")
