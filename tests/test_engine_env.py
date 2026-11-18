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
    (proj / "pkgx" / "show.py").write_text("import pkgx, sys\nprint(pkgx.X, pkgx.__file__, sys.argv[1:])\n")
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
        # 실제 엔진처럼 python_prefix 로 모듈 실행(인자 전달까지)
        engine.reset_python_mode()
        mode = engine.ensure_python()
        out = subprocess.run(engine.python_prefix("pkgx.show") + ["--port", "1"], capture_output=True, text=True)
        assert out.returncode == 0 and out.stdout.startswith("42") and "--port" in out.stdout, (mode, out.stdout, out.stderr)
        print("prefix ok:", mode)
        # 직접 연결(boot) 방식도 동작하는지
        engine._PY_MODE = "boot"
        out = subprocess.run(engine.python_prefix("pkgx.show") + ["--port", "1"], capture_output=True, text=True)
        assert out.returncode == 0 and out.stdout.startswith("42") and "--port" in out.stdout, (out.stdout, out.stderr)
        print("boot ok")
        engine.reset_python_mode()

# ---- 실사용과 같게: 엔진 폴더 안의 uv 관리 Python 3.11 로 만든 가상 환경을 옮김(윈도우 트램펄린 문제 재현)
if uv is not None:
    vdir2 = base / "DTMC_0.1.0"
    eng2 = vdir2 / "engine"
    proj2 = eng2 / "ACE-Step-1.5"
    (proj2 / "pkgx").mkdir(parents=True)
    (vdir2 / "DTMC.exe").write_text("x")
    (proj2 / "pkgx" / "__init__.py").write_text("X = 42\n")
    (proj2 / "pkgx" / "show.py").write_text("import pkgx, sys\nprint(pkgx.X, pkgx.__file__, sys.argv[1:])\n")
    (proj2 / "pyproject.toml").write_text('[project]\nname = "pkgx"\nversion = "0.1"\nrequires-python = ">=3.10"\n'
                                          '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n')
    envm = dict(os.environ, UV_CACHE_DIR=str(base / "uvc"), UV_PYTHON_INSTALL_DIR=str(eng2 / "python"),
                UV_PYTHON_PREFERENCE="only-managed")
    r = subprocess.run([uv, "sync", "-q", "--python", "3.11"], cwd=proj2, capture_output=True, text=True, env=envm)
    if r.returncode != 0:
        print("SKIP managed-python relocate (uv sync 실패):", r.stderr[-200:])
    else:
        (proj2 / "checkpoints" / "acestep-v15-turbo").mkdir(parents=True)
        (eng2 / "installed.json").write_text("{}")
        paths.app_dir = lambda: vdir2
        paths.set_engine_dir(eng2)
        os.environ["DTMC_RELOCATE_TO"] = str(base / "DTMC_engine2")
        moved = engine.relocate()
        assert moved == base / "DTMC_engine2", moved
        assert not eng2.exists()
        mode = engine.ensure_python()
        out = subprocess.run(engine.python_prefix("pkgx.show") + ["--port", "1"], capture_output=True, text=True)
        assert out.returncode == 0 and out.stdout.startswith("42"), (mode, out.stdout, out.stderr)
        assert "DTMC_engine2" in out.stdout and "DTMC_0.1.0" not in out.stdout, out.stdout
        print("managed-python relocate ok:", mode, out.stdout.strip())
        # v0.1.4 가 옮겨 놓은 고장 상태(home = 끊긴 부 버전 링크, 실행기 = 옛 위치)를 다시 만들고 스스로 고치는지
        import re as _re
        venv2 = moved / "ACE-Step-1.5" / ".venv"
        cfgp = venv2 / "pyvenv.cfg"
        txt = cfgp.read_text(encoding="utf-8")
        txt = _re.sub(r"(cpython-\d+\.\d+)\.\d+", r"\1", txt)          # home → 부 버전 링크 이름(없음)
        cfgp.write_text(txt, encoding="utf-8")
        if os.name == "nt":
            bak = venv2 / "Scripts" / "python.uv-trampoline.bak"
            if bak.exists():
                shutil.copy2(bak, venv2 / "Scripts" / "python.exe")      # 옛 위치를 적어 둔 uv 실행기로 되돌림
        else:
            b = venv2 / "bin" / "python"
            b.unlink()
            b.symlink_to(base / "없는곳" / "python3")
        engine.reset_python_mode()
        mode = engine.ensure_python()
        out = subprocess.run(engine.python_prefix("pkgx.show"), capture_output=True, text=True)
        assert out.returncode == 0 and out.stdout.startswith("42"), (mode, out.stdout, out.stderr)
        print("heal ok:", mode)
print("OK test_engine_env")
