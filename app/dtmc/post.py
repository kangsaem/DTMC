"""후보정(v0.1.20): 원곡 칸에 넣은 파일(주로 DTMC 로 만든 곡)을 새로 작곡하지 않고 손질만.

순서 = 악기(음원 분리) → 고치기(엔진: 부분 다시 · 다시 입히기 · 이어 만들기) → 편집 → 음향.
- 편집 · 음향 = ffmpeg 필터(설치 없음, 몇 초).
- 악기 = Demucs(htdemucs, 4개: 보컬 · 드럼 · 베이스 · 나머지) — 엔진 폴더 Python 에 처음 한 번 설치,
  모델(약 80MB)은 처음 쓸 때 인터넷에서 받음. 그래픽카드가 있으면 그것으로.
- 고치기 = jobs.MakeJob 이 엔진 요청으로 처리(여기서는 계획만).
모든 중간 파일 = 48kHz 스테레오 WAV.
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import engine as EG, ff, paths

L = logging.getLogger("dtmc")

STEMS = ("vocals", "drums", "bass", "other")
STEM_KO = {"vocals": "보컬", "drums": "드럼", "bass": "베이스", "other": "나머지"}
GAIN = {"keep": 1.0, "down": 0.5, "mute": 0.0}        # 줄임 = -6dB


@dataclass
class Post:
    """화면 설정 그대로(기본 = 아무것도 안 함)."""
    cut: list = field(default_factory=list)          # trim · bar · loop
    fade_in: int = 0
    fade_out: int = 0
    fit: str = "keep"                                # keep · set
    fit_s: int = 90
    tempo: int = 0                                   # % (-10~10)
    key: int = 0                                     # 반음(-2~2)
    denoise: str = "off"                             # off · low · mid
    clean: list = field(default_factory=list)        # low · high · click · loud
    space: str = "off"                               # off · room · water · wide
    vocals: str = "keep"
    drums: str = "keep"
    bass: str = "keep"
    other: str = "keep"
    stems: bool = False
    repaint: bool = False
    rp_a: int = 30
    rp_b: int = 40
    redo: bool = False
    extend: bool = False
    ext_s: int = 20

    def inst_on(self) -> bool:
        return self.stems or any(getattr(self, k) != "keep" for k in STEMS)

    def fix_on(self) -> bool:
        return self.repaint or self.redo or self.extend

    def edit_on(self) -> bool:
        return bool(self.cut) or self.fade_in > 0 or self.fade_out > 0 or self.fit == "set" or bool(self.tempo) \
            or bool(self.key)

    def sound_on(self) -> bool:
        return self.denoise != "off" or bool(self.clean) or self.space != "off"

    def any_on(self) -> bool:
        return self.inst_on() or self.fix_on() or self.edit_on() or self.sound_on()

    def tag(self) -> str:
        """파일 이름 끝(짧게): 예 '_보컬뺌_다듬기'. 자세한 것은 작업 기록."""
        t = []
        muted = [STEM_KO[k] for k in STEMS if getattr(self, k) == "mute"]
        if len(muted) == 1:
            t.append(muted[0] + "뺌")
        elif muted or any(getattr(self, k) == "down" for k in STEMS):
            t.append("악기")
        if self.repaint:
            t.append("부분다시")
        if self.redo:
            t.append("다시입힘")
        if self.extend:
            t.append("이어")
        if self.edit_on():
            t.append("편집")
        if self.sound_on():
            t.append("다듬기")
        return "_".join(t) or "후보정"

    def summary(self) -> str:
        t = []
        for k in STEMS:
            v = getattr(self, k)
            if v != "keep":
                t.append(STEM_KO[k] + (" 뺌" if v == "mute" else " 줄임"))
        if self.stems:
            t.append("악기별 저장")
        if self.repaint:
            t.append(f"{self.rp_a}~{self.rp_b}초 다시")
        if self.redo:
            t.append("다시 입히기")
        if self.extend:
            t.append(f"{self.ext_s}초 이어")
        t += [{"trim": "무음 자르기", "bar": "마디에서 끝", "loop": "루프"}[k] for k in self.cut]
        if self.fade_in or self.fade_out:
            t.append(f"페이드 {self.fade_in}/{self.fade_out}초")
        if self.fit == "set":
            t.append(f"길이 {self.fit_s}초")
        if self.tempo:
            t.append(f"템포 {self.tempo:+d}%")
        if self.key:
            t.append(f"조 {self.key:+d}")
        if self.denoise != "off":
            t.append("잡음 " + {"low": "약", "mid": "중"}[self.denoise])
        t += [{"low": "저음 정리", "high": "고음 정리", "click": "튀는 소리", "loud": "크기 맞추기"}[k] for k in self.clean]
        if self.space != "off":
            t.append({"room": "울림", "water": "물속", "wide": "넓게"}[self.space])
        return " · ".join(t)


# ------------------------------------------------------------ ffmpeg 공통
def _ff(args, what: str, timeout=900):
    cp = ff.run([paths.ffmpeg(), "-v", "error", "-nostdin", "-y", *args], timeout=timeout)
    ff._check(cp, what)


def to_wav(src, dst) -> Path:
    """아무 형식 → 48kHz 스테레오 WAV."""
    _ff(["-i", str(src), "-vn", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(dst)], "WAV 바꾸기")
    return Path(dst)


def _af(src, dst, chain: str, what: str) -> Path:
    _ff(["-i", str(src), "-vn", "-af", chain, "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(dst)], what)
    return Path(dst)


# ------------------------------------------------------------ 음향(ffmpeg 필터 한 번)
def sound_chain(p: Post) -> str:
    f = []
    if "click" in p.clean:
        f += ["adeclick", "adeclip"]
    if p.denoise != "off":                                   # FFT 잡음 줄이기(히스) — 약 6dB · 중 12dB
        f.append(f"afftdn=nr={6 if p.denoise == 'low' else 12}:nf=-50:tn=1")
    if "low" in p.clean:                                     # 30Hz 아래 웅웅거림
        f.append("highpass=f=30:poles=2")
    if "high" in p.clean:                                    # 귀를 찌르는 고음 · 금속성 살짝 누름
        f += ["equalizer=f=3500:t=q:w=1.2:g=-2", "highshelf=f=9000:g=-3"]
    if p.space == "room":
        f.append("aecho=0.8:0.6:45|70|110:0.22|0.15|0.08")
    elif p.space == "water":                                 # 고음을 깎고 울림을 더함(물속 느낌)
        f += ["lowpass=f=2200:poles=2", "aecho=0.8:0.7:60|95|140:0.3|0.22|0.12", "chorus=0.6:0.8:45:0.35:0.25:2"]
    elif p.space == "wide":
        f.append("extrastereo=m=1.6")
    if "loud" in p.clean:                                    # 압축 → 유튜브 기준 크기(-14 LUFS) → 깨짐 막기
        f += ["acompressor=threshold=-18dB:ratio=2:attack=20:release=250:makeup=2",
              "loudnorm=I=-14:TP=-1.5:LRA=11", "alimiter=limit=0.89"]
    return ",".join(f)


# ------------------------------------------------------------ 편집
def trim_silence(src, dst) -> Path:
    """앞뒤 무음(-50dB 아래) 자르기."""
    chain = ("silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.05,areverse,"
             "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.3,areverse")
    return _af(src, dst, chain, "무음 자르기")


def end_at_bar(src, dst, bpm: float) -> Path:
    """끝을 마디 경계에서 자연스럽게: 마지막 온전한 마디까지 남기고 그 마디 동안 페이드 아웃."""
    d = ff.duration(src)
    bar = 4 * 60.0 / bpm if bpm and bpm > 0 else 2.0
    n = int(d // bar)
    end = n * bar if n >= 2 else d
    fade = min(bar, 4.0, end / 4)
    return _af(src, dst, f"atrim=0:{end:.3f},afade=t=out:st={end - fade:.3f}:d={fade:.3f}", "마디에서 끝내기")


def make_loop(src, dst, xf: float = 2.0) -> Path:
    """루프: 끝 xf 초를 처음 xf 초 위에 겹쳐(크로스페이드) 끝 → 처음이 끊김 없이 이어지게. 길이는 xf 만큼 짧아짐."""
    d = ff.duration(src)
    xf = max(0.2, min(xf, d / 4))
    chain = (f"[0:a]asplit=2[a][b];"
             f"[a]atrim=0:{xf:.3f},asetpts=PTS-STARTPTS,afade=t=in:d={xf:.3f}[head];"
             f"[b]atrim={xf:.3f}:{d:.3f},asetpts=PTS-STARTPTS[body];"
             f"[body]asplit=2[b1][b2];"
             f"[b1]atrim=0:{d - 2 * xf:.3f},asetpts=PTS-STARTPTS[main];"
             f"[b2]atrim={d - 2 * xf:.3f},asetpts=PTS-STARTPTS,afade=t=out:d={xf:.3f}[tail];"
             f"[tail][head]amix=inputs=2:normalize=0[join];"
             f"[main][join]concat=n=2:v=0:a=1[o]")
    _ff(["-i", str(src), "-filter_complex", chain, "-map", "[o]", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le",
         str(dst)], "루프 만들기")
    return Path(dst)


def tempo_key(src, dst, tempo_pct: int, semis: int) -> Path:
    """빠르기(음높이 그대로) · 조(빠르기 그대로). 조 = 표본율을 바꿔 음높이를 옮기고 atempo 로 길이를 되돌림."""
    f = []
    if semis:
        r = 2 ** (semis / 12.0)
        f += [f"asetrate={48000 * r:.1f}", "aresample=48000", f"atempo={1 / r:.6f}"]
    if tempo_pct:
        f.append(f"atempo={1 + tempo_pct / 100.0:.4f}")
    return _af(src, dst, ",".join(f), "빠르기 · 조 바꾸기")


def fades(src, dst, fin: int, fout: int) -> Path:
    d = ff.duration(src)
    f = []
    if fin:
        f.append(f"afade=t=in:st=0:d={min(fin, d / 2):.3f}")
    if fout:
        fo = min(fout, d / 2)
        f.append(f"afade=t=out:st={d - fo:.3f}:d={fo:.3f}")
    return _af(src, dst, ",".join(f), "페이드")


# ------------------------------------------------------------ 악기(Demucs)
STEM_SCRIPT = '''"""DTMC 음원 분리(Demucs htdemucs) — 엔진 폴더 Python 으로 실행: python -m dtmc_stems 입력.wav 출력폴더"""
import sys
import numpy as np
import soundfile as sf
import torch
from demucs.apply import apply_model
from demucs.pretrained import get_model

inp, out = sys.argv[1], sys.argv[2]
m = get_model("htdemucs")
m.eval()
dev = "cuda" if torch.cuda.is_available() else "cpu"
m.to(dev)
wav, sr = sf.read(inp, always_2d=True, dtype="float32")
if wav.shape[1] == 1:
    wav = np.repeat(wav, 2, axis=1)
x = torch.from_numpy(wav.T.copy())[None]
ref = x.mean(1)
mu, sd = ref.mean(), ref.std() + 1e-8
with torch.no_grad():
    y = apply_model(m, ((x - mu) / sd).to(dev), device=dev, split=True, overlap=0.25, progress=False)[0]
y = (y * sd + mu).cpu().numpy()
for name, s in zip(m.sources, y):
    sf.write(f"{out}/{name}.wav", s.T, sr, subtype="PCM_16")
print("DTMC_STEMS_OK", dev, sr, ",".join(m.sources))
'''


def demucs_ready() -> bool:
    return (EG.project() / ".dtmc_demucs_ok").exists()


def ensure_demucs(on_line, should_stop) -> None:
    """엔진 폴더 Python 에 demucs 설치(처음 한 번 · 이미 있는 torch 는 그대로 씀) + 분리 스크립트 두기."""
    (EG.project() / "dtmc_stems.py").write_text(STEM_SCRIPT, encoding="utf-8")
    if demucs_ready():
        return
    venv = EG.project() / ".venv"
    cmd = [paths.uv(), "pip", "install", "--python", str(venv), "demucs"]
    L.info("음원 분리 프로그램 설치: demucs")
    code = EG.stream(cmd, EG.project(), on_line, should_stop)
    if code != 0:
        raise EG.EngineError("음원 분리 프로그램(demucs) 설치에 실패했습니다(engine.log 확인). 인터넷 연결을 확인하세요.")
    (EG.project() / ".dtmc_demucs_ok").write_text("ok", encoding="utf-8")


def separate(src_wav, out_dir, on_line, should_stop) -> dict:
    """4개로 나눔 → {이름: 경로}. 실패 = EngineError."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (EG.project() / "dtmc_stems.py").write_text(STEM_SCRIPT, encoding="utf-8")
    cmd = EG.python_prefix("dtmc_stems") + [str(src_wav), str(out_dir)]
    code = EG.stream(cmd, EG.project(), on_line, should_stop)
    got = {k: out_dir / f"{k}.wav" for k in STEMS}
    if code != 0 or not all(p.is_file() for p in got.values()):
        raise EG.EngineError("악기 나누기에 실패했습니다(engine.log 확인). 처음이면 모델(약 80MB)을 받는 데 인터넷이 필요합니다.")
    return got


def remix(stems: dict, p: Post, dst) -> Path:
    """나눈 4개를 설정대로 다시 섞음(그대로 1 · 줄임 0.5 · 뺌 0)."""
    keep = [(k, GAIN[getattr(p, k)]) for k in STEMS if GAIN[getattr(p, k)] > 0]
    if not keep:
        raise EG.EngineError("악기를 모두 빼면 남는 소리가 없습니다.")
    args, parts = [], []
    for i, (k, g) in enumerate(keep):
        args += ["-i", str(stems[k])]
        parts.append(f"[{i}:a]volume={g}[s{i}]")
    chain = ";".join(parts) + ";" + "".join(f"[s{i}]" for i in range(len(keep))) + \
        f"amix=inputs={len(keep)}:normalize=0[o]"
    _ff([*args, "-filter_complex", chain, "-map", "[o]", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(dst)],
        "악기 다시 섞기")
    return Path(dst)


def copy(src, dst) -> Path:
    shutil.copyfile(src, dst)
    return Path(dst)
