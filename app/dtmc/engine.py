"""생성 엔진(ACE-Step 1.5) 설치·실행·요청.

설치(첫 실행 한 번): 프로그램 폴더 engine_src 의 ACE-Step 소스(고정 버전)를 엔진 폴더로 복사 →
  uv 로 Python 3.11 + 라이브러리(PyTorch CUDA 12.8 포함) 설치(uv.lock 그대로, --frozen) →
  기본 모델(DiT turbo · VAE · 텍스트 인코더 · 언어 모델 1.7B) 다운로드 → 설치 완료 표시(installed.json).
  PyTorch GPU판은 NVIDIA 가 없으면 CPU 로 동작하므로 4090·노트북 모두 같은 설치.
실행: 엔진 폴더의 Python 으로 ACE-Step REST API 서버(127.0.0.1, 빈 포트)를 띄우고 /health 가 될 때까지 기다림.
  DTMC 가 어떻게 끝나든 서버가 남지 않게 윈도우 Job Object 에 넣음(MOSCO 방식).
요청: POST /release_task → /query_result 로 진행 확인 → /v1/audio 로 WAV 받기.
시험용: 환경 변수 DTMC_ENGINE_URL 이 있으면 설치·실행 없이 그 서버를 씀(tests/mock_engine.py).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import hw, log, paths

ACE_COMMIT = "ca1e85fe9430179831e6bc6be790c332190a3866"     # 고정 버전(2026-08-29). 바꾸면 build.yml 도 같이
PYTHON = "3.11"
NEED_GB = 30                                               # 설치에 필요한 여유 공간(라이브러리 + 모델 + 임시)
MAIN_MODEL = "acestep-v15-turbo"
L = log.log


class EngineError(Exception):
    pass


class Cancelled(EngineError):
    pass


# ------------------------------------------------------------ 윈도우 Job Object(MOSCO ff.py 방식)
_job = None
_job_ready = False


def _win_job():
    global _job, _job_ready
    if _job_ready:
        return _job
    _job_ready = True
    want = os.environ.get("DTMC_JOB")
    if want == "0" or os.name != "nt":
        return None
    if want != "1" and not getattr(sys, "frozen", False):
        return None
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class _BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class _IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("Read", "Write", "Other", "ReadT", "WriteT", "OtherT")]

        class _EXT(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", _BASIC), ("IoInfo", _IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        h = k32.CreateJobObjectW(None, None)
        if not h:
            return None
        info = _EXT()
        info.BasicLimitInformation.LimitFlags = 0x2000          # KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):
            k32.CloseHandle(h)
            return None
        _job = (k32, h)
        return _job
    except Exception:  # noqa: BLE001
        return None


def _assign_job(p):
    job = _win_job()
    if not job or p.pid is None:
        return
    try:
        from ctypes import wintypes
        k32, h = job
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        hp = k32.OpenProcess(0x0001 | 0x0100, False, p.pid)
        if hp:
            k32.AssignProcessToJobObject(h, hp)
            k32.CloseHandle(hp)
    except Exception:  # noqa: BLE001
        pass


def kill_tree(p) -> None:
    if p is None or p.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, timeout=20,
                           creationflags=hw.NO_WINDOW)
        else:
            p.kill()
        p.wait(timeout=10)
    except Exception:  # noqa: BLE001
        try:
            p.kill()
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------------ 위치
def project() -> Path:
    return paths.engine_dir() / "ACE-Step-1.5"


def checkpoints() -> Path:
    # ACE-Step 서버 시작 코드(startup_model_init)는 <프로젝트>/checkpoints 를 직접 보므로 그 자리에 둔다
    # (다른 곳에 두면 서버가 모델을 한 번 더 받음).
    return project() / "checkpoints"


def tmp_dir() -> Path:
    d = paths.engine_dir() / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def venv_python() -> Path:
    v = project() / ".venv"
    return v / "Scripts" / "python.exe" if os.name == "nt" else v / "bin" / "python"


def _venv() -> Path:
    return project() / ".venv"


def _cfg() -> dict:
    d = {}
    try:
        for ln in (_venv() / "pyvenv.cfg").read_text(encoding="utf-8").splitlines():
            if "=" in ln:
                k, v = ln.split("=", 1)
                d[k.strip().lower()] = v.strip()
    except Exception:  # noqa: BLE001
        pass
    return d


def base_python() -> Path | None:
    """가상 환경이 기대는 원래 Python(엔진 폴더\\python\\cpython-…\\python.exe)."""
    home = _cfg().get("home")
    if not home:
        return None
    h = Path(home)
    for c in ((h / "python.exe", h / "python3", h / "python") if os.name == "nt" else (h / "python3", h / "python")):
        if c.exists():
            return c
    return None


def _site_packages() -> Path | None:
    v = _venv()
    c = v / "Lib" / "site-packages"
    if c.is_dir():
        return c
    found = sorted(v.glob("lib/python*/site-packages"))
    return found[0] if found else None


def heal() -> None:
    """이미 옮겨진 엔진(v0.1.4 가 옮긴 것 포함)의 경로 고장 고치기: pyvenv.cfg home 을 실제 Python 폴더로 · 끊긴 링크 지움."""
    eng = paths.engine_dir()
    try:
        n = _fix_venv_paths(eng, eng)
        _drop_broken_links(eng)
        if n:
            L.info("엔진 경로 고침(%d개 파일)", n)
    except Exception as e:  # noqa: BLE001
        L.warning("엔진 경로 고치기 실패: %s", e)


def repair_launcher() -> bool:
    """윈도우 uv 가상 환경의 python.exe(트램펄린)는 원래 Python 위치를 exe 안에 적어 둠 → 엔진 폴더를 옮기면 못 찾음
    ('uv trampoline failed to spawn Python child process'). 표준 Python 의 venv 실행기(pyvenv.cfg 의 home 을 읽음)로 바꿔 끼움
    → 이후 다시 옮겨도 pyvenv.cfg 만 고치면 됨. 바꿨으면 True."""
    base = base_python()
    if base is None:
        return False
    if os.name != "nt":                                     # 리눅스·맥: bin/python 이 옛 Python 을 가리키는 링크 → 다시 잇기
        b = _venv() / "bin" / "python"
        try:
            if b.is_symlink() and not b.exists():
                b.unlink()
                b.symlink_to(base)
                return True
        except OSError as e:
            L.warning("bin/python 다시 잇기 실패: %s", e)
        return False
    nt = base.parent / "Lib" / "venv" / "scripts" / "nt"
    pairs = [("python.exe", ("venvlauncher.exe", "python.exe")), ("pythonw.exe", ("venvwlauncher.exe", "pythonw.exe"))]
    done = False
    for dst_name, srcs in pairs:
        src = next((nt / s for s in srcs if (nt / s).exists()), None)
        if src is None:
            continue
        dst = _venv() / "Scripts" / dst_name
        try:
            if dst.exists():
                bak = dst.with_suffix(".uv-trampoline.bak")
                if bak.exists():
                    bak.unlink()
                dst.replace(bak)
            shutil.copy2(src, dst)
            done = True
        except OSError as e:
            L.warning("실행기 바꾸기 실패 %s: %s", dst, e)
    if done:
        L.info("가상 환경 실행기를 표준 venv 실행기로 바꿈(%s)", nt)
    return done


_BOOT = ("import site, sys, runpy; site.addsitedir({sp!r}); sys.prefix = sys.exec_prefix = {venv!r}; "
         "sys.argv[0] = {mod!r}; runpy.run_module({mod!r}, run_name='__main__', alter_sys=True)")
_PY_MODE = None                                              # "venv" | "boot"


def _probe(cmd) -> bool:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=env(), creationflags=hw.NO_WINDOW)
        if r.returncode != 0:
            log.engine_line("python 점검 실패: " + (r.stderr or r.stdout).strip()[-300:])
        return r.returncode == 0
    except Exception as e:  # noqa: BLE001
        log.engine_line(f"python 점검 실패: {e}")
        return False


def ensure_python() -> str:
    """엔진 Python 을 실제로 실행할 수 있게: 가상 환경 python.exe → 안 되면 실행기 바꿔 끼우고 다시 → 그래도 안 되면
    원래 Python + site-packages 직접 연결(boot). 정한 방식을 돌려줌. 다 안 되면 EngineError."""
    global _PY_MODE
    if _PY_MODE:
        return _PY_MODE
    check = ["-c", "import sys; print(sys.prefix)"]
    if _probe([str(venv_python())] + check):
        _PY_MODE = "venv"
        return _PY_MODE
    heal()
    if _probe([str(venv_python())] + check) or (repair_launcher() and _probe([str(venv_python())] + check)):
        _PY_MODE = "venv"
        return _PY_MODE
    base, sp = base_python(), _site_packages()
    if base is not None and sp is not None and _probe(
            [str(base), "-c", f"import site, sys; site.addsitedir({str(sp)!r}); print(sys.prefix)"]):
        L.info("엔진 Python: 원래 Python + site-packages 직접 연결 방식")
        _PY_MODE = "boot"
        return _PY_MODE
    raise EngineError("엔진 Python 을 실행할 수 없습니다(engine.log 확인). 엔진을 다시 설치해야 할 수 있습니다.")


def python_prefix(module: str) -> list[str]:
    """`python -m module` 에 해당하는 명령 앞부분(ensure_python 이 정한 방식대로)."""
    mode = ensure_python()
    if mode == "venv":
        return [str(venv_python()), "-m", module]
    return [str(base_python()), "-c", _BOOT.format(sp=str(_site_packages()), venv=str(_venv()), mod=module)]


def reset_python_mode() -> None:
    global _PY_MODE
    _PY_MODE = None


def marker() -> Path:
    return paths.engine_dir() / "installed.json"


def external_url() -> str:
    return os.environ.get("DTMC_ENGINE_URL", "").rstrip("/")


def installed() -> bool:
    if external_url():
        return True
    try:
        m = json.loads(marker().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False
    return (m.get("commit") == ACE_COMMIT and venv_python().exists()
            and (checkpoints() / MAIN_MODEL).is_dir())


def env() -> dict:
    e = dict(os.environ)
    eng = paths.engine_dir()
    for k in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX"):   # 다른 파이썬 설정이 섞이지 않게
        e.pop(k, None)
    e.update({
        "UV_CACHE_DIR": str(eng / "uv-cache"),
        "UV_PYTHON_INSTALL_DIR": str(eng / "python"),
        "UV_PYTHON_PREFERENCE": "only-managed",
        "UV_LINK_MODE": "copy",
        "UV_NO_PROGRESS": "0",
        "ACESTEP_CHECKPOINTS_DIR": str(checkpoints()),
        "ACESTEP_PROJECT_ROOT": str(project()),
        "ACESTEP_TMPDIR": str(tmp_dir()),
        "HF_HOME": str(eng / "hf"),
        "TORCH_HOME": str(eng / "torch"),
        "TRITON_CACHE_DIR": str(eng / "triton"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PYTHONUNBUFFERED": "1",
        "ACESTEP_DISABLE_TQDM": "0",
    })
    return e


# ------------------------------------------------------------ 명령 실행(출력 한 줄씩)
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def stream(cmd, cwd, on_line, should_stop, envd=None) -> int:
    """명령을 실행하며 출력(\\r·\\n 단위)을 on_line 으로. should_stop() = True 면 프로세스 트리를 끄고 Cancelled."""
    L.info("실행: %s (위치 %s)", " ".join(map(str, cmd)), cwd)
    log.engine_line("$ " + " ".join(map(str, cmd)))
    p = subprocess.Popen([str(c) for c in cmd], cwd=str(cwd), env=envd or env(), stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=hw.NO_WINDOW)
    _assign_job(p)
    buf = b""
    stop = {"v": False}

    def watch():
        while p.poll() is None:
            if should_stop():
                stop["v"] = True
                kill_tree(p)
                return
            time.sleep(0.3)
    threading.Thread(target=watch, daemon=True).start()
    while True:
        ch = p.stdout.read1(4096) if hasattr(p.stdout, "read1") else p.stdout.read(1)
        if not ch:
            break
        buf += ch
        parts = re.split(rb"[\r\n]", buf)
        buf = parts.pop()
        for raw in parts:
            t = _ANSI.sub("", raw.decode("utf-8", "replace")).strip()
            if t:
                log.engine_line(t)
                on_line(t)
    if buf.strip():
        t = _ANSI.sub("", buf.decode("utf-8", "replace")).strip()
        log.engine_line(t)
        on_line(t)
    code = p.wait()
    if stop["v"]:
        raise Cancelled("취소했습니다")
    return code


# ------------------------------------------------------------ 설치
STEPS = [("copy", "소스 복사"), ("sync", "Python·라이브러리 설치"), ("models", "모델 다운로드"), ("clean", "정리")]


def install(on_step, on_line, should_stop) -> None:
    """on_step(키, 순번, 전체) · on_line(글자). 실패 = EngineError(이유)."""
    eng = paths.engine_dir()
    if not paths.plain(eng):
        raise EngineError(f"엔진 폴더 경로에 한글·띄어쓰기가 있으면 설치할 수 없습니다: {eng}")
    eng.mkdir(parents=True, exist_ok=True)
    free = hw.disk_free_gb(eng)
    if free and free < NEED_GB:
        raise EngineError(f"디스크 여유 공간이 부족합니다: {free:g}GB (필요 {NEED_GB}GB 이상, 위치 {eng.anchor})")
    src = paths.engine_src()
    if not (src / "pyproject.toml").exists():
        raise EngineError(f"엔진 소스가 없습니다: {src}")
    uv = paths.uv()
    n = len(STEPS)
    # 1. 소스 복사(이전 설치가 다른 버전이면 .venv 는 두고 소스만 새로)
    on_step("copy", 1, n)
    dst = project()
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name in (".venv", "checkpoints"):
            continue
        t = dst / item.name
        if item.is_dir():
            shutil.copytree(item, t, dirs_exist_ok=True)
        else:
            shutil.copy2(item, t)
    on_line(f"소스 복사 완료 → {dst}")
    if should_stop():
        raise Cancelled("취소했습니다")
    # 2. 라이브러리(uv.lock 그대로)
    on_step("sync", 2, n)
    code = stream([uv, "sync", "--frozen", "--python", PYTHON], dst, on_line, should_stop)
    if code != 0 or not venv_python().exists():
        raise EngineError("Python·라이브러리 설치에 실패했습니다(engine.log 확인). 인터넷 연결을 확인하고 다시 시도하세요.")
    # 3. 모델
    on_step("models", 3, n)
    checkpoints().mkdir(parents=True, exist_ok=True)
    code = stream([venv_python(), "-m", "acestep.model_downloader", "--dir", checkpoints()], dst, on_line, should_stop)
    if code != 0 or not (checkpoints() / MAIN_MODEL).is_dir():
        raise EngineError("모델 다운로드에 실패했습니다(engine.log 확인). 다시 시도하면 받던 곳부터 이어서 받습니다.")
    # 4. 정리(다운로드 임시 파일 지움 — 수 GB)
    on_step("clean", 4, n)
    try:
        stream([uv, "cache", "clean"], dst, on_line, lambda: False)
    except Exception as e:  # noqa: BLE001
        L.warning("uv cache clean 실패: %s", e)
    paths.set_engine_dir(paths.engine_dir())          # 모든 버전이 같은 엔진을 쓰게 기억
    marker().write_text(json.dumps({"commit": ACE_COMMIT, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                                    "python": PYTHON}, ensure_ascii=False), encoding="utf-8")
    on_line("설치 완료")


# ------------------------------------------------------------ 추가 모델(비교 시험)
def model_ready(model: str) -> bool:
    """내려받기가 끝난 모델인지(기본 모델 = 설치 때 받음 · 추가 모델 = 끝나면 표시 파일을 둠)."""
    if external_url() or model == MAIN_MODEL:
        return True
    return (checkpoints() / model / ".dtmc_ok").is_file()


def download_model(model: str, on_line, should_stop) -> None:
    """추가 모델 내려받기(ACE-Step model_downloader, 받던 곳부터 이어받음). 실패 = EngineError."""
    if model_ready(model):
        return
    cmd = python_prefix("acestep.model_downloader") + ["--model", model, "--dir", str(checkpoints()), "--skip-main"]
    L.info("모델 내려받기: %s", model)
    code = stream(cmd, project(), on_line, should_stop)
    d = checkpoints() / model
    if code != 0 or not d.is_dir() or not any(d.iterdir()):
        raise EngineError(f"모델({model}) 내려받기에 실패했습니다(engine.log 확인). 다시 하면 받던 곳부터 이어받습니다.")
    (d / ".dtmc_ok").write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")


# ------------------------------------------------------------ 서버
def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Engine:
    """서버 하나를 띄워 두고 요청. 여러 스레드에서 쓰지 않음(작업 스레드 하나)."""

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.base = external_url()
        self.lines: list[str] = []
        self.profile = hw.profile()
        self._reader = None

    # ---- 실행
    def running(self) -> bool:
        if external_url():
            return True
        return self.proc is not None and self.proc.poll() is None

    def _health(self, timeout=3.0) -> bool:
        """서버가 떠 있고 모델까지 읽었는지(models_initialized 가 False 면 아직)."""
        try:
            d = self._get("/health", timeout) or {}
            return d.get("status") == "ok" and d.get("models_initialized", True) is not False
        except Exception:  # noqa: BLE001
            return False

    def server_env(self) -> dict:
        """엔진 서버 환경 변수(실행 모드·언어 모델·모델 미리 읽기·임시 폴더)."""
        e = env()
        pr = self.profile
        lm_ok = bool(pr.lm_model) and (checkpoints() / pr.lm_model).is_dir()   # 기본 설치 = 1.7B 만 받음
        e["ACESTEP_INIT_LLM"] = "true" if (pr.init_llm and lm_ok) else "false"
        if lm_ok:
            e["ACESTEP_LM_MODEL_PATH"] = pr.lm_model
        if pr.mode == "cpu":
            e["ACESTEP_DEVICE"] = "cpu"
            e["CUDA_VISIBLE_DEVICES"] = ""
        e["ACESTEP_CONFIG_PATH"] = MAIN_MODEL
        # ACE-Step 서버 기본 = 모델을 첫 요청 때 읽음(ACESTEP_NO_INIT 기본 true) → /health 의 models_initialized 가
        # 계속 False 라 '엔진 준비 0%'에서 멈춤(v0.1.2 실기). 시작할 때 읽게 함 → 준비되면 바로 생성.
        e["ACESTEP_NO_INIT"] = "false"
        # 요청의 model 이 지금 읽힌 것과 다르면 서버가 그 모델로 바꿔 읽음(비교 시험의 모델 비교 · 일꾼 1개일 때만 동작)
        e["ACESTEP_ON_DEMAND_MODEL_LOAD"] = "true"
        e["ACESTEP_QUEUE_WORKERS"] = e["ACESTEP_API_WORKERS"] = "1"
        # 서버는 참고 음원(src_audio_path) 절대 경로를 '시스템 임시 폴더' 안에서만 받는다
        # (acestep/api/http/release_task_audio_paths.py). → 서버의 임시 폴더 = 엔진 tmp(참고 음원을 두는 곳)
        t = str(tmp_dir())
        e["TMP"] = e["TEMP"] = e["TMPDIR"] = t
        e["ACESTEP_DOWNLOAD_SOURCE"] = "auto"
        return e

    def start(self, on_line=lambda t: None, should_stop=lambda: False, timeout: float = 1800.0) -> None:
        if external_url():
            self.base = external_url()
            if not self._health(5):
                raise EngineError(f"시험 엔진에 연결할 수 없습니다: {external_url()}")
            return
        if self.running() and self._health():
            return
        self.stop()
        if not installed():
            raise EngineError("생성 엔진이 아직 설치되지 않았습니다.")
        port = _free_port()
        self.base = f"http://127.0.0.1:{port}"
        e = self.server_env()
        pr = self.profile
        cmd = python_prefix("acestep.api_server") + ["--host", "127.0.0.1", "--port", str(port)]
        L.info("엔진 시작(%s): %s", pr.label, " ".join(cmd))
        log.engine_line("$ " + " ".join(cmd))
        self.lines = []
        self.cur_model, self.model_note = MAIN_MODEL, ""
        self.proc = subprocess.Popen(cmd, cwd=str(project()), env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, creationflags=hw.NO_WINDOW)
        _assign_job(self.proc)

        def read(p=self.proc):
            for raw in iter(p.stdout.readline, b""):
                t = _ANSI.sub("", raw.decode("utf-8", "replace")).strip()
                if t:
                    log.engine_line(t)
                    self.lines.append(t)
                    del self.lines[:-200]
                    if "Model loaded on demand:" in t:                    # 서버가 모델을 바꿔 읽음
                        self.cur_model = t.rsplit(":", 1)[1].strip()
                    elif "using primary" in t or "on-demand load of" in t:  # 못 바꿈(조용히 기본 모델로 만듦)
                        self.model_note = t[-200:]
                    try:
                        on_line(t)
                    except Exception:  # noqa: BLE001
                        pass
        self._reader = threading.Thread(target=read, daemon=True, name="engine-out")
        self._reader.start()
        t_end = time.monotonic() + timeout
        while time.monotonic() < t_end:
            if should_stop():
                self.stop()
                raise Cancelled("취소했습니다")
            if self.proc.poll() is not None:
                tail = " / ".join(self.lines[-4:])
                raise EngineError(f"생성 엔진이 시작하다 멈췄습니다(engine.log 확인): {tail}")
            if self._health(2):
                L.info("엔진 준비됨 %s", self.base)
                return
            time.sleep(1.0)
        self.stop()
        raise EngineError("생성 엔진이 제시간에 시작하지 않았습니다(engine.log 확인).")

    def stop(self) -> None:
        if self.proc is not None:
            L.info("엔진 끔")
            kill_tree(self.proc)
            self.proc = None

    # ---- HTTP
    def _req(self, method, path, body=None, timeout=30.0):
        url = self.base + path
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        rq = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(rq, timeout=timeout) as r:     # noqa: S310 — 127.0.0.1 서버
            raw = r.read()
        d = json.loads(raw.decode("utf-8"))
        if isinstance(d, dict) and "data" in d and "code" in d:
            if d.get("code") not in (200, None) or d.get("error"):
                raise EngineError(f"엔진 오류: {d.get('error') or d.get('code')}")
            return d["data"]
        return d

    def _get(self, path, timeout=30.0):
        return self._req("GET", path, None, timeout)

    def submit(self, req: dict) -> str:
        d = self._req("POST", "/release_task", req, 120)
        tid = (d or {}).get("task_id")
        if not tid:
            raise EngineError(f"작업을 넣지 못했습니다: {d}")
        return tid

    def query(self, task_id: str) -> dict:
        d = self._req("POST", "/query_result", {"task_id_list": [task_id]}, 60)
        item = (d or [{}])[0] if isinstance(d, list) else {}
        res = item.get("result") or "[]"
        try:
            res = json.loads(res) if isinstance(res, str) else res
        except Exception:  # noqa: BLE001
            res = []
        prog = 0.0
        err = None
        files = []
        for r in res or []:
            if isinstance(r, dict):
                prog = max(prog, float(r.get("progress") or 0.0))
                err = err or r.get("error")
                if r.get("file"):
                    files.append(r["file"])
        return {"status": int(item.get("status", 0)), "progress": prog, "text": item.get("progress_text") or "",
                "files": files, "error": err, "raw": res}

    def download(self, file_url: str, dst: Path) -> Path:
        url = file_url if file_url.startswith("http") else self.base + file_url
        dst = Path(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=300) as r, open(dst, "wb") as f:   # noqa: S310
            shutil.copyfileobj(r, f)
        return dst

    def _wait(self, tid: str, on_progress, should_stop, timeout: float, what: str) -> dict:
        t0 = time.monotonic()
        while True:
            if should_stop():
                self.stop()                      # 생성 중 취소 = 엔진을 꺼서 바로 멈춤(다음 생성 때 다시 켬)
                raise Cancelled("취소했습니다")
            if not external_url() and self.proc is not None and self.proc.poll() is not None:
                raise EngineError(f"{what} 중에 엔진이 멈췄습니다(메모리 부족일 수 있음, engine.log 확인).")
            q = self.query(tid)
            if q["status"] == 1:
                return q
            if q["status"] == 2:
                raise EngineError(f"{what} 실패: {q['error'] or q['text'] or '이유 모름'}")
            on_progress(q["progress"], q["text"])
            if time.monotonic() - t0 > timeout:
                raise EngineError(f"{what} 시간이 너무 오래 걸려 중단했습니다.")
            time.sleep(1.0)

    def understand(self, req: dict, on_progress, should_stop, timeout: float = 900.0) -> dict:
        """원곡 이해(full_analysis_only) → {caption, genres, bpm, keyscale}. 언어 모델이 켜져 있어야 함."""
        L.info("원곡 이해 요청: %s", json.dumps(req, ensure_ascii=False))
        q = self._wait(self.submit(req), on_progress, should_stop, timeout, "원곡 이해")
        r = next((x for x in q["raw"] or [] if isinstance(x, dict)), {})
        m = r.get("metas") or {}
        out = {"caption": r.get("prompt") or m.get("caption") or "",
               "genres": r.get("genre") or m.get("genres") or m.get("genre") or "",
               "bpm": r.get("bpm") or m.get("bpm"), "keyscale": r.get("keyscale") or m.get("keyscale") or ""}
        L.info("원곡 이해 결과: %s", json.dumps(out, ensure_ascii=False))
        return out

    def generate(self, req: dict, dst_dir: Path, on_progress, should_stop, timeout: float = 3600.0) -> list[Path]:
        """요청 하나(후보 batch_size 곡) → 받은 WAV 경로 목록. on_progress(0~1, 글자)."""
        L.info("생성 요청: %s", json.dumps(req, ensure_ascii=False))
        t0 = time.monotonic()
        q = self._wait(self.submit(req), on_progress, should_stop, timeout, "생성")
        if not q["files"]:
            raise EngineError("생성은 끝났지만 결과 파일이 없습니다.")
        want = req.get("model")
        if want and not external_url() and getattr(self, "cur_model", want) != want:
            # 서버는 모델을 못 바꾸면 말없이 기본 모델로 만든다 → 비교가 틀어지지 않게 여기서 막음
            raise EngineError(f"모델을 {want} 로 바꾸지 못했습니다(engine.log 확인). {getattr(self, 'model_note', '')}")
        out = []
        for i, f in enumerate(q["files"]):
            qs = urllib.parse.urlparse(f).query
            ext = Path(urllib.parse.parse_qs(qs).get("path", ["x.wav"])[0]).suffix or ".wav"
            out.append(self.download(f, Path(dst_dir) / f"cand{i + 1}{ext}"))
        L.info("생성 완료 %d곡 %.1fs", len(out), time.monotonic() - t0)
        return out


# ------------------------------------------------------------ 엔진 옮기기(버전 폴더 밖으로)
def _map_path(text: str, new: Path) -> str:
    """옛 엔진 안의 경로 하나를 새 엔진 경로로 — 글자 비교가 아니라 구조로(윈도우 짧은 이름 RUNNER~1·대소문자가 달라도 됨).
    ...\\ACE-Step-1.5\\나머지 → 새\\ACE-Step-1.5\\나머지 · ...\\python\\cpython-…\\나머지 → 새\\python\\cpython-…\\나머지."""
    t = text.strip()
    parts = re.split(r"[\\/]+", t)
    low = [x.lower() for x in parts]
    if "ace-step-1.5" in low:
        k = low.index("ace-step-1.5")
        return str(new.joinpath("ACE-Step-1.5", *[x for x in parts[k + 1:] if x]))
    for k, x in enumerate(low):
        if x.startswith("cpython-") and k > 0 and low[k - 1] == "python":
            real = _real_python_dir(new, parts[k])
            return str(real.joinpath(*[y for y in parts[k + 1:] if y]))
    return text


def _is_link(p: Path) -> bool:
    try:
        return p.is_symlink() or bool(getattr(p, "is_junction", lambda: False)())
    except OSError:
        return False


def _real_python_dir(new: Path, name: str) -> Path:
    """uv 는 python\\cpython-3.11-…(부 버전 이름)을 실제 폴더 cpython-3.11.18-… 로 잇는 링크(윈도우 = 정션)로 만들고
    가상 환경은 그 링크를 가리킴. 링크는 옛 위치를 절대 경로로 가리켜 옮기면 끊김 → 실제 폴더를 직접 가리키게."""
    root = new / "python"
    cand = root / name
    if cand.is_dir() and not _is_link(cand):
        return cand
    m = re.match(r"(cpython-\d+\.\d+)(-.*)$", name, re.IGNORECASE)
    if m:
        hits = sorted(d for d in root.glob(f"{m.group(1)}.*{m.group(2)}") if d.is_dir() and not _is_link(d))
        if hits:
            return hits[-1]
    return cand


def _drop_broken_links(new: Path) -> None:
    """옮긴 엔진의 python 폴더에서 끊긴 링크(옛 위치를 가리키는 부 버전 링크) 지움 — 필요하면 uv 가 다시 만듦."""
    root = new / "python"
    if not root.is_dir():
        return
    for d in root.iterdir():
        if _is_link(d) and not d.exists():
            try:
                os.unlink(d)
            except OSError:
                try:
                    os.rmdir(d)
                except OSError as e:
                    L.warning("끊긴 링크 지우기 실패 %s: %s", d, e)


def _fix_venv_paths(new: Path, old: Path) -> int:
    """옮긴 뒤 가상 환경 안의 옛 경로를 새 경로로: pyvenv.cfg 의 home(엔진 Python) · *.pth 의 경로 줄(ACE-Step 소스 위치)."""
    import glob
    venv = new / "ACE-Step-1.5" / ".venv"
    n = 0
    cfg = venv / "pyvenv.cfg"
    try:
        lines = cfg.read_text(encoding="utf-8").splitlines()
        out = []
        for ln in lines:
            if "=" in ln and ln.split("=", 1)[0].strip().lower() in ("home", "base-executable", "base-prefix", "base-exec-prefix"):
                k, v = ln.split("=", 1)
                nv = _map_path(v, new)
                if nv != v:
                    ln = f"{k.rstrip()} = {nv}"
            out.append(ln)
        if out != lines:
            cfg.write_text("\n".join(out) + "\n", encoding="utf-8")
            n += 1
    except Exception as e:  # noqa: BLE001
        L.warning("pyvenv.cfg 고치기 실패: %s", e)
    pths = []
    for pat in ("Lib/site-packages/*.pth", "lib/python*/site-packages/*.pth"):
        pths += [Path(f) for f in glob.glob(str(venv / pat))]
    for f in pths:
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except Exception:  # noqa: BLE001
            continue
        out = [ln if ln.startswith(("import ", "#")) or not ln.strip() else _map_path(ln, new) for ln in lines]
        if out != lines:
            f.write_text("\n".join(out) + "\n", encoding="utf-8")
            n += 1
    del old
    return n


def relocate() -> Path | None:
    """엔진이 DTMC 버전 폴더 안에 있으면 같은 드라이브의 \\DTMC_engine 으로 옮김(이름 바꾸기라 몇 초, 다시 받지 않음).
    이미 거기 엔진이 있으면 그걸 쓰고 옛 것은 그대로 둠. 실패해도 그 자리에서 계속 씀. 옮긴 곳 또는 None."""
    from . import procs
    d = paths.engine_dir()
    if os.environ.get("DTMC_ENGINE_DIR") or not paths.inside_version_folder(d) or not paths._has_engine(d):
        return None
    target = Path(d.anchor or str(d.parent.parent)) / "DTMC_engine"
    if os.environ.get("DTMC_RELOCATE_TO"):                  # 시험용
        target = Path(os.environ["DTMC_RELOCATE_TO"])
    if target.exists():
        if paths._has_engine(target):
            L.info("엔진: 고정 위치에 이미 있음 → %s 사용(옛 %s 는 그대로)", target, d)
            paths.set_engine_dir(target)
            return target
        try:
            target.rmdir()                                  # 빈 폴더면 지우고 진행
        except OSError:
            L.warning("엔진 옮기기 건너뜀: %s 가 비어 있지 않음", target)
            return None
    procs.kill_under([d])                                   # 옛 실행이 남긴 엔진 프로세스(파일 잠금) 정리
    try:
        os.rename(d, target)
    except OSError as e:
        L.warning("엔진 옮기기 실패(%s → %s): %s — 그 자리에서 계속 씀", d, target, e)
        return None
    n = _fix_venv_paths(target, d)
    _drop_broken_links(target)
    paths.set_engine_dir(target)
    reset_python_mode()
    repair_launcher()                                       # 윈도우: uv 실행기는 옛 위치를 exe 안에 적어 둠 → 표준 실행기로
    L.info("엔진 옮김: %s → %s (경로 고친 파일 %d개)", d, target, n)
    return target
