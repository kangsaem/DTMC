"""원곡 분석(numpy 만 사용, 추가 라이브러리 없음): 템포(BPM) · 조성(장/단조) · 길이 · 크기 · 밝기 → 분위기.

- 템포: 스펙트럼 변화량(onset) 곡선의 자기상관, 120 BPM 근처를 조금 선호(사람이 느끼는 박 쪽으로).
- 조성: 크로마(12음 세기) 평균을 Krumhansl 장·단조 표와 상관 → 가장 맞는 조.
- 분위기: 템포·장단조·크기·밝기·음 밀도로 규칙 판정(사용자가 화면에서 고칠 수 있음).
결과는 생성 지시문(caption)·BPM·조성(key_scale)으로 쓰인다. 원곡 멜로디는 쓰지 않는다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

SR = 22050
N_FFT = 2048
HOP = 512
MAX_SEC = 240.0                                     # 앞 4분만 분석(충분하고 빠름)
NOTE = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


@dataclass
class Analysis:
    duration: float = 0.0
    bpm: float = 0.0
    root: int = 0                     # 0 = C
    minor: bool = False
    key_conf: float = 0.0             # 0~1
    loud_db: float = -60.0
    dyn_db: float = 0.0
    centroid: float = 0.0             # Hz
    density: float = 0.0              # 초당 음 시작 수
    moods: list = field(default_factory=list)

    @property
    def key_scale(self) -> str:       # ACE-Step 형식: "C major", "F# minor"
        return f"{NOTE[self.root]} {'minor' if self.minor else 'major'}"

    @property
    def key_label(self) -> str:       # 화면: "C 장조"
        return f"{NOTE[self.root]} {'단조' if self.minor else '장조'}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Analysis":
        a = cls()
        for k, v in (d or {}).items():
            if hasattr(a, k):
                setattr(a, k, v)
        return a


def _frames(x: np.ndarray) -> np.ndarray:
    if len(x) < N_FFT:
        x = np.pad(x, (0, N_FFT - len(x)))
    v = np.lib.stride_tricks.sliding_window_view(x, N_FFT)[::HOP]
    return v


def _spec(x: np.ndarray) -> np.ndarray:
    win = np.hanning(N_FFT).astype(np.float32)
    fr = _frames(x)
    out = np.empty((fr.shape[0], N_FFT // 2 + 1), np.float32)
    step = 512
    for i in range(0, fr.shape[0], step):            # 조금씩(메모리 아낌)
        out[i:i + step] = np.abs(np.fft.rfft(fr[i:i + step] * win, axis=1))
    return out


def _tempo(mag: np.ndarray) -> tuple[float, np.ndarray]:
    fps = SR / HOP
    freqs = np.fft.rfftfreq(N_FFT, 1 / SR)
    m = np.log1p(100.0 * mag[:, freqs < 8000])
    flux = np.maximum(0.0, np.diff(m, axis=0)).sum(axis=1)
    if len(flux) < 16:
        return 0.0, flux
    k = max(1, int(fps * 0.5))
    local = np.convolve(flux, np.ones(k) / k, mode="same")
    env = np.maximum(0.0, flux - local)
    env = env - env.mean()
    n = len(env)
    spec = np.fft.rfft(env, 2 * n)
    ac = np.fft.irfft(spec * np.conj(spec))[:n]
    if ac[0] <= 0:
        return 0.0, flux
    ac = ac / ac[0]
    bpms = np.arange(50.0, 200.5, 0.5)
    lags = 60.0 * fps / bpms
    vals = np.interp(lags, np.arange(n), ac)
    # 같은 박의 배수(2배·절반 박)도 함께 보며 120 근처를 선호(log-normal, 1옥타브 폭)
    vals2 = np.interp(lags * 2, np.arange(n), ac, right=0.0)
    score = (vals + 0.5 * vals2) * np.exp(-0.5 * (np.log2(bpms / 120.0) / 1.0) ** 2)
    i = int(np.argmax(score))
    if 0 < i < len(score) - 1:
        a, b, c = score[i - 1], score[i], score[i + 1]
        d = a - 2 * b + c
        off = 0.5 * (a - c) / d if d != 0 else 0.0
        bpm = bpms[i] + off * 0.5
    else:
        bpm = bpms[i]
    return float(bpm), env


def _key(mag: np.ndarray) -> tuple[int, bool, float]:
    freqs = np.fft.rfftfreq(N_FFT, 1 / SR)
    sel = (freqs >= 65.0) & (freqs <= 2100.0)
    f = freqs[sel]
    pc = np.mod(np.round(69 + 12 * np.log2(f / 440.0)).astype(int), 12)
    m = np.sqrt(mag[:, sel])
    chroma_t = np.zeros((m.shape[0], 12), np.float32)
    for c in range(12):
        chroma_t[:, c] = m[:, pc == c].sum(axis=1)
    e = chroma_t.sum(axis=1)
    keep = e > (np.percentile(e, 30) if len(e) > 10 else 0)
    if not keep.any():
        return 0, False, 0.0
    ct = chroma_t[keep] / (chroma_t[keep].max(axis=1, keepdims=True) + 1e-9)
    chroma = ct.mean(axis=0)
    scores = []
    for r in range(12):
        scores.append((np.corrcoef(chroma, np.roll(MAJOR, r))[0, 1], r, False))
        scores.append((np.corrcoef(chroma, np.roll(MINOR, r))[0, 1], r, True))
    scores.sort(reverse=True)
    best, second = scores[0], scores[1]
    conf = float(np.clip((best[0] - second[0]) * 5 + max(0.0, best[0]) * 0.5, 0, 1))
    return best[1], best[2], conf


def _moods(a: Analysis) -> list[str]:
    out = []
    if a.bpm >= 118 and (a.loud_db > -20 or a.density > 3.0):
        out.append("energetic")
    if a.bpm and a.bpm < 92 and a.density < 2.5:
        out.append("calm")
    if a.minor:
        out.append("sad")
    elif a.centroid > 1800:
        out.append("bright")
    if a.centroid and a.centroid < 1300:
        out.append("warm")
    if a.dyn_db > 22 and a.loud_db > -24:
        out.append("epic")
    if "calm" in out and a.centroid > 1500 and not a.minor:
        out.append("dreamy")
    if not out:
        out.append("calm" if a.bpm < 105 else "bright")
    return out[:3]


def analyze_pcm(x: np.ndarray, duration: float) -> Analysis:
    a = Analysis(duration=float(duration))
    if x.size < SR:                                  # 1초 미만 = 분석 생략
        a.moods = ["calm"]
        return a
    x = x.astype(np.float32)
    mag = _spec(x)
    a.bpm, env = _tempo(mag)
    a.root, a.minor, a.key_conf = _key(mag)
    rms = np.sqrt(np.mean(_frames(x).astype(np.float32) ** 2, axis=1)) + 1e-9
    db = 20 * np.log10(rms)
    loud = db[db > np.percentile(db, 50)]
    a.loud_db = float(np.mean(loud)) if loud.size else float(np.mean(db))
    a.dyn_db = float(np.percentile(db, 95) - np.percentile(db, 10))
    freqs = np.fft.rfftfreq(N_FFT, 1 / SR)
    s = mag.sum(axis=1) + 1e-9
    cen = (mag * freqs).sum(axis=1) / s
    a.centroid = float(np.median(cen[s > np.percentile(s, 30)])) if len(s) > 10 else float(np.median(cen))
    if env.size:
        thr = env.mean() + env.std()
        peaks = (env[1:-1] > thr) & (env[1:-1] >= env[:-2]) & (env[1:-1] >= env[2:])
        a.density = float(peaks.sum() / (len(env) / (SR / HOP)))
    a.bpm = round(a.bpm, 1)
    a.moods = _moods(a)
    return a


def analyze_file(path) -> Analysis:
    from . import ff
    dur = ff.duration(path)
    start = 0.0
    if dur > MAX_SEC + 20:                           # 긴 곡: 앞 인트로를 조금 건너뜀
        start = min(20.0, dur * 0.05)
    x = ff.decode_mono(path, SR, MAX_SEC, start)
    return analyze_pcm(x, dur)
