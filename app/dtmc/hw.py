"""컴퓨터 사양 확인: NVIDIA GPU(nvidia-smi) · 메모리 · CPU 이름 · 디스크 여유 → 실행 모드(GPU 고품질 / CPU 경량)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass

NO_WINDOW = 0x08000000 if os.name == "nt" else 0          # CREATE_NO_WINDOW
CUDA_MIN_DRIVER = 570                                       # PyTorch cu128(CUDA 12.8) 에 필요한 드라이버 대략 최소


@dataclass
class Gpu:
    name: str
    vram_gb: float
    driver: str

    def driver_major(self) -> int:
        try:
            return int(self.driver.split(".")[0])
        except Exception:  # noqa: BLE001
            return 0


def gpu() -> Gpu | None:
    if os.environ.get("DTMC_FORCE_CPU") == "1":
        return None
    exe = shutil.which("nvidia-smi")
    if not exe and os.name == "nt":
        cand = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvidia-smi.exe")
        exe = cand if os.path.exists(cand) else None
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=8, creationflags=NO_WINDOW).stdout
        line = out.strip().splitlines()[0]
        name, mem, drv = [x.strip() for x in line.split(",")[:3]]
        return Gpu(name, round(float(mem) / 1024, 1), drv)
    except Exception:  # noqa: BLE001
        return None


def ram_gb() -> float:
    try:
        if os.name == "nt":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("avail", ctypes.c_ulonglong), ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                            ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("ext", ctypes.c_ulonglong)]
            m = MS()
            m.len = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return round(m.total / 1024 ** 3, 1)
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 1024 ** 2, 1)
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def cpu_name() -> str:
    try:
        if os.name == "nt":
            import winreg
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            return str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
        if sys.platform.startswith("linux"):
            with open("/proc/cpuinfo", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
    except Exception:  # noqa: BLE001
        pass
    return "CPU"


def disk_free_gb(path) -> float:
    from pathlib import Path
    p = Path(path)
    while not p.exists() and p.parent != p:
        p = p.parent
    try:
        return round(shutil.disk_usage(p).free / 1024 ** 3, 1)
    except Exception:  # noqa: BLE001
        return 0.0


@dataclass
class Profile:
    mode: str                 # "gpu" | "cpu"
    label: str                # 화면 표시
    init_llm: bool            # 언어 모델(곡 설계) 켜기 — text2music 품질↑, 메모리↑
    lm_model: str
    thinking: bool
    candidates: int           # 후보 기본값
    warn: str = ""            # 주의(드라이버 낮음·메모리 적음 등)

    @property
    def lm_ready(self) -> bool:
        """언어 모델을 실제로 켤 수 있는지(설치된 모델이 있어야 함)."""
        if not (self.init_llm and self.lm_model):
            return False
        try:
            from . import engine
            return (engine.checkpoints() / self.lm_model).is_dir() or bool(engine.external_url())
        except Exception:  # noqa: BLE001
            return False


_PROFILE: Profile | None = None


def profile(refresh: bool = False) -> Profile:
    """GPU 메모리 12GB 이상 = 언어 모델 1.7B 켬, 6GB 이상 = 0.6B, 그 밖 = 끔(ACE-Step 권장표). NVIDIA 없음 = CPU 경량."""
    global _PROFILE
    if _PROFILE is not None and not refresh:
        return _PROFILE
    g = gpu()
    ram = ram_gb()
    if g is not None:
        warn = ""
        if 0 < g.driver_major() < CUDA_MIN_DRIVER:
            warn = f"그래픽 드라이버({g.driver})가 오래됐습니다. NVIDIA 드라이버를 최신으로 업데이트하세요."
        if g.vram_gb >= 12:
            lm, on = "acestep-5Hz-lm-1.7B", True
        elif g.vram_gb >= 6:
            lm, on = "acestep-5Hz-lm-0.6B", True
        else:
            lm, on = "", False
        _PROFILE = Profile("gpu", f"GPU · {g.name} {g.vram_gb:g}GB", on, lm, on, 2, warn)
    else:
        warn = ""
        if 0 < ram < 15:
            warn = f"메모리 {ram:g}GB — 권장 16GB보다 적어 생성이 실패하거나 매우 느릴 수 있습니다."
        _PROFILE = Profile("cpu", f"CPU · {cpu_name()} · RAM {ram:g}GB", False, "", False, 1, warn)
    return _PROFILE
