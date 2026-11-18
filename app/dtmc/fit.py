"""원곡 길이 맞추기(v0.1.13 시험): 원곡을 마디 단위로 재배치해 목표 길이로.

- 줄이기: 가운데의 연속한 마디 묶음 하나를 뺌 / 늘리기: 한 묶음을 한 번(또는 여러 번) 더 넣음.
- 어디를 빼거나 반복할지 = 이어 붙인 자리 앞뒤 마디의 화음(크로마)이 원래 흐름과 가장 닮은 곳.
- 박 위치는 템포가 일정하다고 보고 격자를 맞춤(템포가 흔들리는 실제 연주는 뒤로 갈수록 어긋날 수 있음).
numpy 만 사용. 결과(Fit)는 ff.arrange 로 실제 소리를 만들고, 이음매는 엔진 repaint 로 다시 그릴 수 있다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

SR = 11025
N_FFT = 2048
HOP = 512
MAX_SEC = 900.0
EDGE_BARS = 4                         # 처음·끝 몇 마디는 건드리지 않음


@dataclass
class Fit:
    segments: list = field(default_factory=list)      # 원곡 시간 (시작, 끝) 차례대로
    seams: list = field(default_factory=list)         # 결과 시간에서 이어 붙인 자리
    length: float = 0.0
    bar: float = 0.0
    note: str = ""

    def changed(self) -> bool:
        return len(self.segments) > 1


def _features(x: np.ndarray):
    """(onset 곡선, 프레임별 크로마 12, 초당 프레임 수)."""
    if len(x) < N_FFT:
        x = np.pad(x, (0, N_FFT - len(x)))
    fr = np.lib.stride_tricks.sliding_window_view(x, N_FFT)[::HOP]
    win = np.hanning(N_FFT).astype(np.float32)
    freqs = np.fft.rfftfreq(N_FFT, 1 / SR)
    sel = (freqs >= 65.0) & (freqs <= 2100.0)
    pc = np.mod(np.round(69 + 12 * np.log2(freqs[sel] / 440.0)).astype(int), 12)
    n = fr.shape[0]
    flux = np.zeros(n, np.float32)
    chroma = np.zeros((n, 12), np.float32)
    prev = None
    for i in range(0, n, 512):
        mag = np.abs(np.fft.rfft(fr[i:i + 512] * win, axis=1)).astype(np.float32)
        m = np.log1p(100.0 * mag)
        full = m if prev is None else np.vstack([prev, m])
        d = np.maximum(0.0, np.diff(full, axis=0)).sum(axis=1)
        flux[i + (0 if prev is not None else 1):i + m.shape[0]] = d
        prev = m[-1:]
        sq = np.sqrt(mag[:, sel])
        for c in range(12):
            chroma[i:i + mag.shape[0], c] = sq[:, pc == c].sum(axis=1)
    return flux, chroma, SR / HOP


def _cos(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(a @ b / (na * nb)) if na > 0 and nb > 0 else 0.0


def plan(x: np.ndarray, duration: float, bpm: float, target: float) -> Fit:
    """모노 PCM(SR) · 길이 · 템포 → 목표 길이에 맞춘 재배치 계획."""
    duration, target = float(duration), float(target)
    beat = 60.0 / bpm if bpm and bpm > 0 else 0.5
    bar = 4 * beat
    whole = Fit([(0.0, duration)], [], duration, bar, "그대로")
    if x.size < SR * 4 or duration < bar * (2 * EDGE_BARS + 2):
        return whole
    flux, chroma, fps = _features(x.astype(np.float32))
    # 박 위상: 박 격자 위의 onset 합이 가장 큰 곳
    best_p, best_v = 0.0, -1.0
    for p in np.arange(0.0, beat, beat / 24):
        idx = ((p + np.arange(0, int((duration - p) / beat)) * beat) * fps).astype(int)
        idx = idx[idx < len(flux)]
        v = float(flux[idx].mean()) if idx.size else 0.0
        if v > best_v:
            best_p, best_v = p, v

    def bars_at(phase):
        nb = int((duration - phase) / bar)
        cs = np.zeros((nb, 12), np.float32)
        for i in range(nb):
            a, b = int((phase + i * bar) * fps), int((phase + (i + 1) * bar) * fps)
            if b > a:
                cs[i] = chroma[a:min(b, len(chroma))].mean(axis=0)
        return cs
    # 마디 위상(박 4개 중 어디가 첫 박인지): 마디 사이 화음 변화가 가장 큰 쪽
    best_ph, best_c, best_n = best_p, None, -1.0
    for k in range(4):
        ph = best_p + k * beat
        cs = bars_at(ph)
        if len(cs) < 2 * EDGE_BARS + 2:
            continue
        nov = float(np.mean([1 - _cos(cs[i], cs[i + 1]) for i in range(len(cs) - 1)]))
        if nov > best_n:
            best_ph, best_c, best_n = ph, cs, nov
    if best_c is None:
        return whole
    cs, ph, n = best_c, best_ph, len(best_c)
    t = lambda i: ph + i * bar                                            # noqa: E731 — 마디 i 의 시작 시각
    delta = duration - target
    k = int(round(abs(delta) / bar))
    if k < 1:
        return whole
    lo, hi = EDGE_BARS, n - EDGE_BARS
    if delta > 0:                                                         # 줄이기: 마디 [i, i+k) 를 뺌
        k = min(k, hi - lo - 1)
        if k < 1:
            return whole
        cand = [(_cos(cs[i - 1], cs[i + k - 1]) + _cos(cs[i], cs[i + k]), i) for i in range(lo, hi - k)]
        if not cand:
            return whole
        _s, i = max(cand)
        segs = [(0.0, t(i)), (t(i + k), duration)]
        note = f"{k}마디 뺌({t(i):.1f}~{t(i + k):.1f}초)"
    else:                                                                 # 늘리기: 마디 [j-m, j) 를 r번 더
        max_m = max(1, hi - lo - 1)
        r = int(np.ceil(k / max_m))
        m = max(1, int(round(k / r)))
        cand = [(_cos(cs[j - 1], cs[j - m - 1]) + _cos(cs[j], cs[j - m]), j) for j in range(lo + m, hi)]
        if not cand:
            return whole
        _s, j = max(cand)
        segs = [(0.0, t(j))] + [(t(j - m), t(j))] * r + [(t(j), duration)]
        note = f"{m}마디를 {r}번 더({t(j - m):.1f}~{t(j):.1f}초)"
    segs = [(float(a), float(b)) for a, b in segs]
    seams, pos = [], 0.0
    for q, (a, b) in enumerate(segs[:-1]):
        pos += b - a
        if abs(segs[q + 1][0] - b) > 1e-6:                                 # 실제로 건너뛰는 자리만(이어지는 곳은 이음매 아님)
            seams.append(round(pos, 3))
    length = sum(b - a for a, b in segs)
    return Fit([(round(a, 3), round(b, 3)) for a, b in segs], seams, round(float(length), 3), float(bar), note)


def plan_file(path, bpm: float, target: float) -> Fit:
    from . import ff
    dur = ff.duration(path)
    x = ff.decode_mono(path, SR, min(dur, MAX_SEC))
    return plan(x, min(dur, MAX_SEC), bpm, target)


def repaint_windows(f: Fit, lo: float = 3.0, hi: float = 20.0) -> list:
    """이음매마다 다시 그릴 구간(결과 시간): 앞뒤 2마디씩, 3~20초."""
    out = []
    for s in f.seams:
        w = min(hi / 2, max(lo / 2, 2 * f.bar))
        a, b = max(0.0, s - w), min(f.length, s + w)
        if b - a >= lo:
            out.append((round(float(a), 2), round(float(b), 2)))
    return out
