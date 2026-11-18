"""작업(백그라운드 스레드): 원곡 분석 · 생성(엔진 준비 → 원곡마다 생성 → 형식 변환·저장) · 악기 시험 · 비교 시험."""
from __future__ import annotations

import json
import random
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from . import analyze, engine as EG, ff, hw, log, paths, prompt, settings

L = log.log
STEP_NAME = {"engine": "엔진 준비", "analyze": "분석", "generate": "생성"}
TEST_SECONDS = 20


@dataclass
class Source:
    path: str
    name: str = ""
    duration: float = 0.0
    analysis: analyze.Analysis | None = None
    error: str = ""
    checked: bool = True
    busy: bool = False

    def __post_init__(self):
        self.name = self.name or Path(self.path).stem


@dataclass
class Plan:
    """[AUTO] 한 번의 설정(화면 값 그대로)."""
    analyze_on: bool = True
    moods: list = field(default_factory=list)       # 비어 있으면 원곡마다 분석 결과
    bpm_mode: str = "src"                           # src = 원곡 템포 · set = 직접
    bpm: int = 90
    instruments: list = field(default_factory=lambda: ["piano"])
    length_mode: str = "src"                        # src = 원곡 길이 · set = 직접
    seconds: int = 60
    strength: int = prompt.DEFAULT_STRENGTH          # 원곡 반영 %(0 = 소리 안 보냄)
    candidates: int = 1
    fmt: str = "mp3"
    out_dir: str = ""
    title: str = ""                                  # 파일 이름 앞부분(비우면 원곡 이름 첫 단어)

    def __post_init__(self):
        self.strength = prompt.strength_pct(self.strength)       # 옛 low/mid/high 도 받음

    def target_seconds(self, src: Source | None) -> float:
        if self.length_mode == "src" and src is not None and src.duration:
            return prompt.clamp_len(src.duration)
        return prompt.clamp_len(self.seconds)


# ------------------------------------------------------------ 이름·기록
safe = prompt.safe


def unique(p: Path) -> Path:
    if not p.exists():
        return p
    for i in range(2, 1000):
        q = p.with_name(f"{p.stem} ({i}){p.suffix}")
        if not q.exists():
            return q
    return p


def write_history(rec: dict) -> None:
    try:
        f = paths.history_dir() / (time.strftime("%Y-%m") + ".jsonl")
        with open(f, "a", encoding="utf-8") as fp:
            fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        L.warning("작업 기록 실패: %s", e)


# ------------------------------------------------------------ 생성 속도(예상 시간)
def _speed_key() -> str:
    return "speed_" + hw.profile().mode


def speed() -> float | None:
    """오디오 1초를 만드는 데 걸린 시간(초) — 지난 생성에서 잰 값. 없으면 None."""
    v = settings.get(_speed_key())
    return float(v) if v else None


def remember_speed(audio_sec: float, took: float) -> None:
    if audio_sec <= 0:
        return
    s = took / audio_sec
    old = speed()
    settings.put(_speed_key(), round(s if old is None else old * 0.5 + s * 0.5, 3))


def estimate(total_audio_sec: float) -> float | None:
    s = speed()
    if s is None:
        return None
    start = settings.get("start_" + hw.profile().mode) or 60.0
    return total_audio_sec * s + float(start)


# ------------------------------------------------------------ 분석
class AnalyzeJob(QThread):
    """넣은 원곡을 차례로 분석(가벼움, CPU). done(경로, 분석 dict|None, 길이, 오류)."""
    done = Signal(str, object, float, str)

    def __init__(self, items: list[str], parent=None):
        super().__init__(parent)
        self.items = list(items)
        self.stop = False

    def run(self):
        for p in self.items:
            if self.stop:
                return
            try:
                a = analyze.analyze_file(p)
                self.done.emit(p, a, a.duration, "")
            except Exception as e:  # noqa: BLE001
                L.warning("분석 실패 %s: %s", p, e)
                self.done.emit(p, None, 0.0, str(e))


# ------------------------------------------------------------ 생성
class MakeJob(QThread):
    """sources 를 plan 대로 생성. test=True 면 원곡 없이 악기마다 20초씩(악기 시험).
    step(키, 단계 안 0~1, 글자) · item(결과 dict) · finished_ok(성공?, 글자)."""
    step = Signal(str, float, str)
    item = Signal(dict)
    finished_ok = Signal(bool, str)

    def __init__(self, eng: EG.Engine, sources: list[Source], plan: Plan, test: bool = False,
                 compare: bool = False, parent=None):
        """test = 악기 시험 · compare = 비교 시험(첫 원곡 앞 30초 × 반영 30·50·70·90%, 같은 씨앗)."""
        super().__init__(parent)
        self.eng, self.sources, self.plan, self.test, self.compare = eng, list(sources), plan, test, compare
        self.cancel = False
        self.outputs: list[Path] = []

    def stopped(self) -> bool:
        return self.cancel

    def steps(self) -> list[str]:
        if self.test or self.compare:
            return ["engine", "generate"]
        return ["engine", "analyze", "generate"] if self.plan.analyze_on else ["engine", "generate"]

    def run(self):
        t_all = time.monotonic()
        try:
            self._run()
            n = len(self.outputs)
            took = time.monotonic() - t_all
            self.finished_ok.emit(True, f"{n}곡 저장 · {int(took // 60)}분 {int(took % 60)}초")
        except EG.Cancelled:
            self.finished_ok.emit(False, "취소했습니다")
        except Exception as e:  # noqa: BLE001
            L.exception("생성 작업 실패")
            self.finished_ok.emit(False, str(e))

    def _engine(self):
        self.step.emit("engine", 0.0, "엔진 켜는 중(처음에는 모델을 읽느라 몇 분 걸릴 수 있음)")
        t0 = time.monotonic()
        was = self.eng.running()

        def line(t):
            if '"GET /' in t or '"POST /' in t or "HTTP/1.1" in t:     # 서버 접속 기록은 숨김(엔진 로그에는 남음)
                return
            self.step.emit("engine", 0.0, t[:120])
        self.eng.start(line, self.stopped)
        if not was:
            settings.put("start_" + hw.profile().mode, round(time.monotonic() - t0, 1))
        self.step.emit("engine", 1.0, "엔진 준비됨")

    def _out_dir(self) -> Path:
        d = Path(self.plan.out_dir) if self.plan.out_dir else paths.default_out_dir()
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _run(self):
        p = self.plan
        out_dir = self._out_dir()
        self._engine()
        if self.test:
            return self._run_test(out_dir)
        if self.compare:
            return self._run_compare(out_dir)
        srcs = [s for s in self.sources if s.checked and not s.error]
        if not srcs:
            raise EG.EngineError("만들 원곡이 없습니다.")
        if p.analyze_on:
            for i, s in enumerate(srcs):
                if self.cancel:
                    raise EG.Cancelled("취소했습니다")
                self.step.emit("analyze", i / len(srcs), s.name)
                if s.analysis is None:
                    s.analysis = analyze.analyze_file(s.path)
                    s.duration = s.analysis.duration
            self.step.emit("analyze", 1.0, "")
        work = EG.tmp_dir() / time.strftime("job_%Y%m%d_%H%M%S")
        try:
            for i, s in enumerate(srcs):
                self._make_one(i, len(srcs), s, work, out_dir)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _moods(self, s: Source) -> list:
        if self.plan.moods:
            return list(self.plan.moods)
        if self.plan.analyze_on and s.analysis is not None:
            return list(s.analysis.moods)
        return ["calm"]

    def _make_one(self, i: int, n: int, s: Source, work: Path, out_dir: Path):
        p = self.plan
        a = s.analysis if p.analyze_on else None
        sec = p.target_seconds(s)
        cover = p.strength > 0
        # 반영 > 0 = 템포·조성은 원곡 그대로(보내지 않음)
        bpm = None if cover else (p.bpm if p.bpm_mode == "set" else (a.bpm if a is not None and a.bpm else None))
        key = "" if cover else (a.key_scale if a is not None and a.key_conf >= 0.15 else "")
        src_path = None
        wd = work / f"{i + 1}"
        wd.mkdir(parents=True, exist_ok=True)
        if cover:
            self.step.emit("generate", i / n, f"{s.name} · 참고 음원 준비")
            src_path = ff.make_source(s.path, wd / "src.wav", sec)
        moods = self._moods(s)
        req = prompt.request(instr_keys=p.instruments, mood_keys=moods, seconds=sec, bpm=bpm, key_scale=key,
                             strength=p.strength, src_path=src_path, candidates=p.candidates, profile=hw.profile())
        label = f"{i + 1}/{n} {s.name}"
        t0 = time.monotonic()

        def prog(f, text):
            el = time.monotonic() - t0
            self.step.emit("generate", (i + min(0.95, max(0.0, f))) / n, f"{label} · {int(el)}초" + (f" · {text[:60]}" if text else ""))
        wavs = self.eng.generate(req, wd, prog, self.stopped)
        remember_speed(sec * len(wavs), time.monotonic() - t0)
        self.step.emit("generate", (i + 0.97) / n, f"{label} · 저장")
        ilab = prompt.instr_label(p.instruments)
        saved = []
        for k, w in enumerate(wavs):
            stem = prompt.out_stem(p.title, s.name, p.instruments, k + 1, sec)
            dst = unique(out_dir / (stem + ff.FORMATS[p.fmt][0]))
            out = ff.convert(w, dst, p.fmt)
            self.outputs.append(out)
            saved.append(str(out))
            self.item.emit({"path": str(out), "source": s.name, "src_path": s.path, "src_dur": s.duration,
                            "strength": p.strength, "instr": ilab, "n": k + 1,
                            "of": len(wavs), "seconds": sec})
        write_history({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "source": s.path, "outputs": saved,
                       "instruments": p.instruments, "moods": moods, "strength": p.strength,
                       "cover_strength": req.get("audio_cover_strength"),
                       "noise_strength": req.get("cover_noise_strength"), "seconds": sec,
                       "bpm": req.get("bpm"), "key": req.get("key_scale"), "task": req.get("task_type"),
                       "caption": req.get("prompt"), "mode": hw.profile().mode,
                       "analysis": a.to_dict() if a is not None else None})
        self.step.emit("generate", (i + 1) / n, f"{label} 완료")

    def _run_test(self, out_dir: Path):
        keys = [k for k, _, _ in prompt.INSTRUMENTS]
        d = out_dir / time.strftime("악기시험_%Y%m%d_%H%M")
        d.mkdir(parents=True, exist_ok=True)
        work = EG.tmp_dir() / time.strftime("test_%Y%m%d_%H%M%S")
        try:
            for i, k in enumerate(keys):
                if self.cancel:
                    raise EG.Cancelled("취소했습니다")
                ko = prompt.INSTR[k][0]
                req = prompt.request(instr_keys=[k], mood_keys=["calm", "warm"], seconds=TEST_SECONDS, bpm=80,
                                     key_scale="C major", strength=0, candidates=1, profile=hw.profile())
                t0 = time.monotonic()

                def prog(f, text, i=i, ko=ko, t0=t0):
                    self.step.emit("generate", (i + min(0.95, f)) / len(keys),
                                   f"{i + 1}/{len(keys)} {ko} · {int(time.monotonic() - t0)}초")
                wavs = self.eng.generate(req, work / k, prog, self.stopped)
                remember_speed(TEST_SECONDS, time.monotonic() - t0)
                out = ff.convert(wavs[0], d / f"{i + 1:02d}_{ko}{ff.FORMATS[self.plan.fmt][0]}", self.plan.fmt)
                self.outputs.append(out)
                self.item.emit({"path": str(out), "source": "악기 시험", "instr": ko, "n": 1, "of": 1,
                                "seconds": TEST_SECONDS})
                write_history({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "source": "악기 시험", "outputs": [str(out)],
                               "instruments": [k], "caption": req["prompt"], "mode": hw.profile().mode})
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _run_compare(self, out_dir: Path):
        """첫 원곡 앞 30초를 반영 30·50·70·90% 로 하나씩 — 같은 씨앗(차이 = 반영 % 만)."""
        p = self.plan
        srcs = [s for s in self.sources if s.checked and not s.error]
        if not srcs:
            raise EG.EngineError("비교할 원곡이 없습니다.")
        s = srcs[0]
        if s.analysis is None:
            s.analysis = analyze.analyze_file(s.path)
            s.duration = s.analysis.duration
        sec = float(prompt.COMPARE_SECONDS)
        seed = random.randint(1, 2 ** 31 - 1)
        moods = self._moods(s)
        work = EG.tmp_dir() / time.strftime("cmp_%Y%m%d_%H%M%S")
        work.mkdir(parents=True, exist_ok=True)
        pcts = prompt.COMPARE_STRENGTHS
        ilab = prompt.instr_label(p.instruments)
        try:
            src_path = ff.make_source(s.path, work / "src.wav", sec)
            for i, pct in enumerate(pcts):
                if self.cancel:
                    raise EG.Cancelled("취소했습니다")
                req = prompt.request(instr_keys=p.instruments, mood_keys=moods, seconds=sec, strength=pct,
                                     src_path=src_path, candidates=1, profile=hw.profile(), seed=seed)
                t0 = time.monotonic()

                def prog(f, text, i=i, pct=pct, t0=t0):
                    self.step.emit("generate", (i + min(0.95, max(0.0, f))) / len(pcts),
                                   f"{i + 1}/{len(pcts)} {s.name} · 반영 {pct}% · {int(time.monotonic() - t0)}초")
                wavs = self.eng.generate(req, work / f"{pct}", prog, self.stopped)
                remember_speed(sec, time.monotonic() - t0)
                stem = prompt.out_stem(p.title, s.name, p.instruments, f"반영{pct}", sec)
                out = ff.convert(wavs[0], unique(out_dir / (stem + ff.FORMATS[p.fmt][0])), p.fmt)
                self.outputs.append(out)
                self.item.emit({"path": str(out), "source": s.name, "src_path": s.path, "src_dur": s.duration,
                                "strength": pct, "instr": ilab, "seed": seed, "n": 1, "of": 1,
                                "seconds": sec})
                write_history({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": "compare", "source": s.path,
                               "outputs": [str(out)], "instruments": p.instruments, "moods": moods,
                               "strength": pct, "cover_strength": req.get("audio_cover_strength"),
                               "noise_strength": req.get("cover_noise_strength"), "seed": seed, "seconds": sec,
                               "caption": req.get("prompt"), "mode": hw.profile().mode})
                self.step.emit("generate", (i + 1) / len(pcts), f"{i + 1}/{len(pcts)} 반영 {pct}% 완료")
        finally:
            shutil.rmtree(work, ignore_errors=True)
