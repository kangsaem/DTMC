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
print("OK test_engine_env")
