"""설정(내 문서\\DTMC\\settings.json). 마지막 선택(악기·길이·형식·저장 폴더 등)과 측정한 생성 속도를 기억."""
from __future__ import annotations

import json
import threading

_lock = threading.Lock()
_cache: dict | None = None


def _path():
    from . import paths
    return paths.settings_path()


def load() -> dict:
    global _cache
    with _lock:
        if _cache is None:
            try:
                _cache = json.loads(_path().read_text(encoding="utf-8"))
                if not isinstance(_cache, dict):
                    _cache = {}
            except Exception:  # noqa: BLE001
                _cache = {}
        return _cache


def get(key: str, default=None):
    return load().get(key, default)


def put(key: str, value) -> None:
    d = load()
    with _lock:
        d[key] = value
        try:
            p = _path()
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(p)
        except Exception:  # noqa: BLE001
            pass
