"""ffmpeg: 원곡 읽기(분석용 PCM) · 원곡을 목표 길이로 맞춘 참고 음원 · 결과 형식 변환(MP3·FLAC·M4A·OGG·WAV)."""
from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

import numpy as np

from . import paths
from .hw import NO_WINDOW

AUDIO_EXT = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".aiff", ".aif"}

# 형식 → (확장자, ffmpeg 인코딩 인자)
FORMATS = {
    "mp3": (".mp3", ["-c:a", "libmp3lame", "-b:a", "320k"]),
    "flac": (".flac", ["-c:a", "flac", "-compression_level", "5"]),
    "m4a": (".m4a", ["-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart"]),
    "ogg": (".ogg", ["-c:a", "libvorbis", "-q:a", "6"]),
    "wav": (".wav", ["-c:a", "pcm_s16le"]),
}
FORMAT_NAMES = [("mp3", "MP3"), ("flac", "FLAC"), ("m4a", "M4A"), ("ogg", "OGG"), ("wav", "WAV")]

_procs: set = set()
_plock = threading.Lock()


def _popen(args, **kw):
    p = subprocess.Popen(args, creationflags=NO_WINDOW, **kw)
    with _plock:
        _procs.add(p)
    return p


def _done(p):
    with _plock:
        _procs.discard(p)


def kill_all():
    with _plock:
        ps = list(_procs)
    for p in ps:
        try:
            p.kill()
        except Exception:  # noqa: BLE001
            pass


def run(args, timeout=None) -> subprocess.CompletedProcess:
    p = _popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        out, err = p.communicate()
        raise RuntimeError(f"ffmpeg 시간 초과: {' '.join(map(str, args[:6]))}") from None
    finally:
        _done(p)
    return subprocess.CompletedProcess(args, p.returncode, out, err)


def _check(cp, what):
    if cp.returncode != 0:
        msg = cp.stderr.decode("utf-8", "replace").strip().splitlines()[-3:] if cp.stderr else []
        raise RuntimeError(f"{what} 실패: " + " / ".join(msg))


def duration(path) -> float:
    cp = run([paths.ffprobe(), "-v", "error", "-show_entries", "format=duration", "-of",
              "default=noprint_wrappers=1:nokey=1", str(path)], timeout=60)
    _check(cp, "길이 읽기")
    try:
        return float(cp.stdout.decode().strip().splitlines()[0])
    except Exception:  # noqa: BLE001
        raise RuntimeError("길이를 읽을 수 없습니다(오디오가 아닌 파일일 수 있음)") from None


def decode_mono(path, sr: int = 22050, max_sec: float | None = None, start: float = 0.0) -> np.ndarray:
    """모노 float32 PCM(분석용)."""
    args = [paths.ffmpeg(), "-v", "error", "-nostdin"]
    if start > 0:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(path), "-vn", "-ac", "1", "-ar", str(sr)]
    if max_sec:
        args += ["-t", f"{max_sec:.3f}"]
    args += ["-f", "f32le", "-"]
    cp = run(args, timeout=600)
    _check(cp, "원곡 읽기")
    return np.frombuffer(cp.stdout, dtype=np.float32).copy()


def make_source(src, dst, seconds: float) -> Path:
    """원곡을 목표 길이로: 길면 앞에서 자르고, 짧으면 반복해서 채움. 끝 2초 페이드 아웃. 48kHz 스테레오 WAV."""
    seconds = max(1.0, float(seconds))
    fade = min(2.0, seconds / 4)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    cp = run([paths.ffmpeg(), "-v", "error", "-nostdin", "-y", "-stream_loop", "-1", "-i", str(src), "-vn",
              "-t", f"{seconds:.3f}", "-af", f"afade=t=out:st={seconds - fade:.3f}:d={fade:.3f}",
              "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(dst)], timeout=600)
    _check(cp, "참고 음원 만들기")
    return dst


def make_ref(files, dst, total: float = 90.0) -> Path:
    """음색 참고 음원 1~3개 → 한 파일(각 total/n 초씩 이어 붙임, 짧으면 반복). 48kHz 스테레오 WAV.
    엔진은 이 파일을 3등분해 앞·중·뒤에서 10초씩 뽑아 음색·연주·녹음 질감만 참고한다."""
    files = [str(f) for f in files][:3]
    each = total / max(1, len(files))
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    args = [paths.ffmpeg(), "-v", "error", "-nostdin", "-y"]
    for f in files:
        args += ["-stream_loop", "-1", "-t", f"{each:.3f}", "-i", f]
    chain = "".join(f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}];" for i in range(len(files)))
    chain += "".join(f"[a{i}]" for i in range(len(files))) + f"concat=n={len(files)}:v=0:a=1[o]"
    cp = run(args + ["-filter_complex", chain, "-map", "[o]", "-c:a", "pcm_s16le", str(dst)], timeout=600)
    _check(cp, "음색 참고 음원 만들기")
    return dst


def convert(src, dst, fmt: str) -> Path:
    ext, codec = FORMATS[fmt]
    dst = Path(dst).with_suffix(ext)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.stem + ".part" + ext)
    cp = run([paths.ffmpeg(), "-v", "error", "-nostdin", "-y", "-i", str(src), "-vn", "-map_metadata", "-1",
              *codec, str(tmp)], timeout=900)
    _check(cp, f"{fmt.upper()} 저장")
    os.replace(tmp, dst)
    return dst


def make_test_tone(dst, seconds: float = 8.0, bpm: float = 120.0) -> Path:
    """시험용 음원: C 장조 아르페지오 + 박마다 타악(분석·변환 점검용)."""
    sr = 44100
    n = int(sr * seconds)
    t = np.arange(n) / sr
    beat = 60.0 / bpm
    notes = [261.63, 329.63, 392.0, 523.25]
    y = np.zeros(n, np.float32)
    k = 0
    pos = 0.0
    while pos < seconds:
        a = int(pos * sr)
        b = min(n, int((pos + beat) * sr))
        tt = t[a:b] - pos
        f = notes[k % len(notes)]
        env = np.exp(-tt * 3.0)
        y[a:b] += (0.3 * np.sin(2 * np.pi * f * tt) * env).astype(np.float32)
        click = min(b, a + int(0.03 * sr))
        y[a:click] += (0.5 * np.random.default_rng(k).standard_normal(click - a) * np.linspace(1, 0, click - a)).astype(np.float32)
        k += 1
        pos += beat
    y = np.clip(y, -1, 1)
    pcm = (y * 32767).astype("<i2").tobytes()
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    import wave
    with wave.open(str(dst), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    return dst
