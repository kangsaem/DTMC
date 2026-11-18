"""엔진 위치 찾기(다른 버전 폴더에 설치한 엔진 이어 쓰기) · 서버 환경 변수 점검. 성공하면 'OK test_engine_env'."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
os.environ["DTMC_USER_DIR"] = tempfile.mkdtemp(prefix="dtmc_user_")
os.environ.pop("DTMC_ENGINE_DIR", None)
os.environ.pop("DTMC_ENGINE_URL", None)

from dtmc import engine, paths, settings  # noqa: E402

base = Path(tempfile.mkdtemp(prefix="dtmc_ver_"))
old = base / "DTMC_0.1.2"
new = base / "DTMC_0.1.3"
(old / "engine").mkdir(parents=True)
(old / "engine" / "installed.json").write_text("{}", encoding="utf-8")
(old / "DTMC.exe").write_text("x")                    # DTMC 버전 폴더 표시
(base / "남의폴더" / "engine").mkdir(parents=True)        # DTMC.exe 없는 옆 폴더 = 무시
(base / "남의폴더" / "engine" / "installed.json").write_text("{}")
new.mkdir()
paths.app_dir = lambda: new            # 새 버전을 다른 폴더에 풀었다고 가정

d = paths.engine_dir()
assert d == old / "engine", d
assert settings.get("engine_dir") == str(old / "engine")
paths._ENGINE_DIR = None
assert paths.engine_dir() == old / "engine"           # 설정에 기억한 곳

paths.set_engine_dir(base / "E")
assert paths.engine_dir() == base / "E"

e = engine.Engine().server_env()
assert e["ACESTEP_NO_INIT"] == "false", e.get("ACESTEP_NO_INIT")
assert e["TMP"] == e["TEMP"] == e["TMPDIR"] == str(engine.tmp_dir())
assert e["ACESTEP_CHECKPOINTS_DIR"] == str(engine.project() / "checkpoints")
assert e["ACESTEP_INIT_LLM"] == "false"               # 언어 모델이 설치 안 됐으면 끔

# ---- 엔진 옮기기(버전 폴더 안 → 고정 위치): 진짜 uv 가상 환경으로 import 가 되는지
import shutil  # noqa: E402
import subprocess  # noqa: E402

uv = shutil.which("uv")
if uv is None:
    print("SKIP relocate (uv 없음)")
else:
    vdir = base / "DTMC_0.1.1"
    proj = vdir / "engine" / "ACE-Step-1.5"
    (proj / "pkgx").mkdir(parents=True)
    (vdir / "DTMC.exe").write_text("x")
    (proj / "pkgx" / "__init__.py").write_text("X = 42\n")
    (proj / "pyproject.toml").write_text('[project]\nname = "pkgx"\nversion = "0.1"\nrequires-python = ">=3.10"\n'
                                         '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n')
    r = subprocess.run([uv, "sync", "-q"], cwd=proj, capture_output=True, text=True,
                       env=dict(os.environ, UV_CACHE_DIR=str(base / "uvc")))
    if r.returncode != 0:
        print("SKIP relocate (uv sync 실패):", r.stderr[-300:])
    else:
        (proj / "checkpoints" / "acestep-v15-turbo").mkdir(parents=True)
        (vdir / "engine" / "installed.json").write_text("{}")
        paths.app_dir = lambda: vdir
        paths.set_engine_dir(vdir / "engine")
        os.environ["DTMC_RELOCATE_TO"] = str(base / "DTMC_engine")
        moved = engine.relocate()
        assert moved == base / "DTMC_engine", moved
        assert not (vdir / "engine").exists() and paths.engine_dir() == moved
        py = engine.venv_python()
        out = subprocess.run([str(py), "-c", "import pkgx, sys; print(pkgx.X, pkgx.__file__)"], capture_output=True, text=True)
        assert out.returncode == 0 and out.stdout.startswith("42"), (out.stdout, out.stderr)
        assert "DTMC_engine" in out.stdout and "DTMC_0.1.1" not in out.stdout, out.stdout
        print("relocate ok:", out.stdout.strip())
        assert engine.relocate() is None                  # 이미 밖에 있으면 아무것도 안 함
print("OK test_engine_env")
