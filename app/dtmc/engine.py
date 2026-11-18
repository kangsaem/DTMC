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
    marker().write_text(json.dumps({"commit": ACE_COMMIT, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                                    "python": PYTHON}, ensure_ascii=False), encoding="utf-8")
    on_line("설치 완료")


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
        try:
            d = self._get("/health", timeout)
            return (d or {}).get("status") == "ok"
        except Exception:  # noqa: BLE001
            return False

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
        e = env()
        pr = self.profile
        e["ACESTEP_INIT_LLM"] = "true" if pr.init_llm else "false"
        if pr.lm_model:
            e["ACESTEP_LM_MODEL_PATH"] = pr.lm_model
        if pr.mode == "cpu":
            e["ACESTEP_DEVICE"] = "cpu"
            e["CUDA_VISIBLE_DEVICES"] = ""
        e["ACESTEP_CONFIG_PATH"] = MAIN_MODEL
        e["ACESTEP_DOWNLOAD_SOURCE"] = "auto"
        cmd = [str(venv_python()), "-m", "acestep.api_server", "--host", "127.0.0.1", "--port", str(port)]
        L.info("엔진 시작(%s): %s", pr.label, " ".join(cmd))
        log.engine_line("$ " + " ".join(cmd))
        self.lines = []
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

    def generate(self, req: dict, dst_dir: Path, on_progress, should_stop, timeout: float = 3600.0) -> list[Path]:
        """요청 하나(후보 batch_size 곡) → 받은 WAV 경로 목록. on_progress(0~1, 글자)."""
        L.info("생성 요청: %s", json.dumps({k: v for k, v in req.items() if k != "lyrics"}, ensure_ascii=False))
        tid = self.submit(req)
        t0 = time.monotonic()
        while True:
            if should_stop():
                self.stop()                      # 생성 중 취소 = 엔진을 꺼서 바로 멈춤(다음 생성 때 다시 켬)
                raise Cancelled("취소했습니다")
            if not external_url() and self.proc is not None and self.proc.poll() is not None:
                raise EngineError("생성 중에 엔진이 멈췄습니다(메모리 부족일 수 있음, engine.log 확인).")
            q = self.query(tid)
            if q["status"] == 1:
                break
            if q["status"] == 2:
                raise EngineError(f"생성 실패: {q['error'] or q['text'] or '이유 모름'}")
            on_progress(q["progress"], q["text"])
            if time.monotonic() - t0 > timeout:
                raise EngineError("생성 시간이 너무 오래 걸려 중단했습니다.")
            time.sleep(1.0)
        if not q["files"]:
            raise EngineError("생성은 끝났지만 결과 파일이 없습니다.")
        out = []
        for i, f in enumerate(q["files"]):
            qs = urllib.parse.urlparse(f).query
            ext = Path(urllib.parse.parse_qs(qs).get("path", ["x.wav"])[0]).suffix or ".wav"
            out.append(self.download(f, Path(dst_dir) / f"cand{i + 1}{ext}"))
        L.info("생성 완료 %d곡 %.1fs", len(out), time.monotonic() - t0)
        return out
