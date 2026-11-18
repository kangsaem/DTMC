"""시험용 가짜 ACE-Step API 서버(실제 모델 없이 사인파 WAV 를 돌려줌).

python tests/mock_engine.py [포트]  → DTMC_ENGINE_URL=http://127.0.0.1:포트 로 DTMC 가 이 서버를 씀.
ACE-Step docs/en/API.md 의 응답 모양(data/code/error 감싸기, result = JSON 문자열)을 그대로 흉내 낸다.
"""
from __future__ import annotations

import json
import math
import os
import struct
import sys
import tempfile
import threading
import time
import uuid
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

TASKS: dict = {}
REQUESTS: list = []
OUT = tempfile.mkdtemp(prefix="dtmc_mock_")
DELAY = float(os.environ.get("MOCK_DELAY", "1.5"))


def wrap(data, code=200, error=None):
    return {"data": data, "code": code, "error": error, "timestamp": int(time.time() * 1000), "extra": None}


def make_wav(path, seconds, freq):
    sr = 16000
    n = int(sr * seconds)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        frames = bytearray()
        for i in range(n):
            frames += struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / sr)))
        w.writeframes(bytes(frames))


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/health":
            return self._send(wrap({"status": "ok", "service": "ACE-Step API", "version": "mock"}))
        if u.path == "/_requests":
            return self._send(REQUESTS)
        if u.path == "/v1/audio":
            p = parse_qs(u.query).get("path", [""])[0]
            if not p.startswith(OUT) or not os.path.exists(p):
                return self._send(wrap(None, 404, "not found"), 404)
            data = open(p, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return None
        return self._send(wrap(None, 404, "unknown"), 404)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/release_task":
            req = self._body()
            REQUESTS.append(req)
            if req.get("task_type") in ("cover", "repaint") or req.get("full_analysis_only"):
                sp = req.get("src_audio_path", "")
                if not os.path.exists(sp):
                    return self._send(wrap(None, 400, "src_audio_path not found"))
                # 실제 서버와 같은 규칙: 절대 경로는 서버 임시 폴더(= DTMC 엔진 tmp) 안만
                allow = os.environ.get("MOCK_TEMP") or os.path.join(os.environ.get("DTMC_ENGINE_DIR", ""), "tmp")
                if os.path.commonpath([os.path.realpath(allow), os.path.realpath(sp)]) != os.path.realpath(allow):
                    return self._send(wrap(None, 400, "absolute audio file paths are not allowed"))
            rp = req.get("reference_audio_path")
            if rp and not os.path.exists(rp):
                return self._send(wrap(None, 400, "reference_audio_path not found"))
            tid = str(uuid.uuid4())
            TASKS[tid] = {"t": time.time(), "req": req, "files": None}
            return self._send(wrap({"task_id": tid, "status": "queued", "queue_position": 1}))
        if u.path == "/query_result":
            ids = self._body().get("task_id_list") or []
            out = []
            for tid in ids:
                t = TASKS.get(tid)
                if t is None:
                    continue
                el = time.time() - t["t"]
                if el < DELAY:
                    res = [{"file": "", "status": 0, "progress": el / DELAY, "stage": "running", "error": None}]
                    out.append({"task_id": tid, "status": 0, "result": json.dumps(res), "progress_text": "diffusion"})
                    continue
                if t["req"].get("full_analysis_only"):      # 원곡 이해: 실제 서버처럼 결과 한 덩어리
                    res = [{"status_message": "Full Hardware Analysis Success", "bpm": 110, "keyscale": "A major",
                            "genre": "J-pop, anime, orchestral",
                            "prompt": "A warm and nostalgic orchestral pop track with strings and drums, hopeful mood.",
                            "metas": {}, "audio_paths": []}]
                    out.append({"task_id": tid, "status": 1, "result": json.dumps(res), "progress_text": "done"})
                    continue
                if t["files"] is None:
                    files = []
                    dur = min(12.0, float(t["req"].get("audio_duration") or 10))
                    for k in range(int(t["req"].get("batch_size") or 1)):
                        p = os.path.join(OUT, f"{tid}_{k}.wav")
                        make_wav(p, dur, 220 * (k + 1))
                        files.append(p)
                    t["files"] = files
                res = [{"file": "/v1/audio?path=" + f, "status": 1, "metas": {"bpm": t["req"].get("bpm")}}
                       for f in t["files"]]
                out.append({"task_id": tid, "status": 1, "result": json.dumps(res), "progress_text": "done"})
            return self._send(wrap(out))
        return self._send(wrap(None, 404, "unknown"), 404)


def serve(port=0):
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


if __name__ == "__main__":
    s, url = serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8765)
    print(url, flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
