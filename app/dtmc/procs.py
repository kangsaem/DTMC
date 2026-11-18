"""남은 프로세스 정리 — 이전 버전 폴더가 '사용 중'이라 안 지워지는 문제 방지.

1) 자기 자신을 윈도우 Job Object(KILL_ON_JOB_CLOSE)에 넣음 → DTMC 가 어떻게 끝나든(정상·강제 종료·오류)
   DTMC 가 띄운 모든 하위 프로세스(엔진 Python 과 그 안의 Python, uv, ffmpeg)가 OS 에 의해 같이 꺼짐.
   (엔진 Python 은 실행되면 자기 안에서 Python 을 하나 더 띄우므로, 자식만 따로 넣는 방식으로는 손자가 빠짐)
   폴더 열기(explorer)는 Job 밖으로 띄움(BREAKAWAY) → DTMC 를 꺼도 탐색기 창은 남음.
2) 시작·종료 때 엔진 폴더(와 옛 버전 폴더의 engine·bin\\uv.exe) 안 실행 파일로 도는 프로세스를 찾아 끔.
윈도우가 아니면 아무것도 안 함. 환경 변수 DTMC_JOB=0 = Job 안 씀(CI 러너).
"""
from __future__ import annotations

import os
import subprocess
import sys

from . import log

L = log.log
BREAKAWAY = 0x01000000                     # CREATE_BREAKAWAY_FROM_JOB
_job = None


def _k32():
    import ctypes
    return ctypes.WinDLL("kernel32", use_last_error=True)


def join_job() -> bool:
    """이 프로세스를 KILL_ON_JOB_CLOSE Job 에 넣음(자식·손자 자동 포함). 성공 = True."""
    global _job
    if os.name != "nt" or _job is not None:
        return _job is not None
    want = os.environ.get("DTMC_JOB")
    if want == "0" or (want != "1" and not getattr(sys, "frozen", False)):
        return False
    try:
        import ctypes
        from ctypes import wintypes
        k32 = _k32()

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
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        h = k32.CreateJobObjectW(None, None)
        if not h:
            return False
        info = _EXT()
        info.BasicLimitInformation.LimitFlags = 0x2000 | 0x0800      # KILL_ON_JOB_CLOSE | BREAKAWAY_OK
        if not k32.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):
            return False
        if not k32.AssignProcessToJobObject(h, k32.GetCurrentProcess()):
            L.warning("Job 넣기 실패(%s) — 하위 프로세스는 종료 때 직접 정리", ctypes.get_last_error())
            return False
        _job = h                                # 핸들을 끝까지 쥐고 있음(닫히면 = DTMC 종료 = 하위 전부 종료)
        return True
    except Exception as e:  # noqa: BLE001
        L.warning("Job 준비 실패: %s", e)
        return False


def launch_outside(args) -> None:
    """Job 밖으로 실행(탐색기 등 DTMC 를 꺼도 남아야 하는 것)."""
    try:
        subprocess.Popen(args, creationflags=BREAKAWAY if os.name == "nt" else 0)
    except Exception:  # noqa: BLE001
        try:
            subprocess.Popen(args)
        except Exception:  # noqa: BLE001
            pass


def _list_images() -> list[tuple[int, str]]:
    """(pid, 실행 파일 전체 경로) 목록 — 윈도우 ctypes(EnumProcesses + QueryFullProcessImageNameW)."""
    import ctypes
    from ctypes import wintypes
    k32 = _k32()
    psapi = ctypes.WinDLL("psapi")
    arr = (wintypes.DWORD * 8192)()
    got = wintypes.DWORD()
    if not psapi.EnumProcesses(ctypes.byref(arr), ctypes.sizeof(arr), ctypes.byref(got)):
        return []
    n = got.value // ctypes.sizeof(wintypes.DWORD)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                               ctypes.POINTER(wintypes.DWORD)]
    out = []
    buf = ctypes.create_unicode_buffer(1024)
    for i in range(n):
        pid = arr[i]
        if not pid or pid == os.getpid():
            continue
        h = k32.OpenProcess(0x1000, False, pid)            # QUERY_LIMITED_INFORMATION
        if not h:
            continue
        size = wintypes.DWORD(len(buf))
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            out.append((pid, buf.value))
        k32.CloseHandle(h)
    return out


def _terminate(pid: int) -> bool:
    from ctypes import wintypes
    k32 = _k32()
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    h = k32.OpenProcess(0x0001 | 0x00100000, False, pid)   # TERMINATE | SYNCHRONIZE
    if not h:
        return False
    ok = bool(k32.TerminateProcess(h, 1))
    k32.WaitForSingleObject(h, 5000)
    k32.CloseHandle(h)
    return ok


def kill_under(folders, exact=()) -> int:
    """실행 파일이 folders 안에 있거나 exact 와 같은 프로세스를 끔. 끈 개수."""
    if os.name != "nt":
        return 0
    pre = [os.path.normcase(os.path.abspath(str(f))).rstrip("\\/") + os.sep for f in folders if f]
    ex = {os.path.normcase(os.path.abspath(str(e))) for e in exact if e}
    n = 0
    try:
        for pid, img in _list_images():
            p = os.path.normcase(os.path.abspath(img))
            if p in ex or any(p.startswith(x) for x in pre):
                if _terminate(pid):
                    n += 1
                    L.info("남은 프로세스 정리: %s (pid %s)", img, pid)
    except Exception as e:  # noqa: BLE001
        L.warning("프로세스 정리 실패: %s", e)
    return n


def cleanup_leftovers() -> int:
    """엔진 폴더 · 옛 버전 폴더의 engine · 옛 버전 bin\\uv.exe 로 도는 프로세스(이전 실행이 남긴 것) 정리."""
    from . import paths
    folders = [paths.engine_dir()] + [d for d in paths.engine_candidates()]
    exact = []
    try:
        for sib in paths.app_dir().parent.iterdir():
            if sib.is_dir() and (sib / "DTMC.exe").exists():
                exact.append(sib / "bin" / "uv.exe")
    except Exception:  # noqa: BLE001
        pass
    return kill_under(folders, exact)
