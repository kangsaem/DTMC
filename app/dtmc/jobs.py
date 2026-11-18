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
    candidates: int = 1
    fmt: str = "mp3"
    out_dir: str = ""
    title: str = ""                                  # 파일 이름 앞부분(비우면 원곡 이름 첫 단어)
    refs: list = field(default_factory=list)         # 음색 참고 음원(실제 악기 녹음, 최대 3개) — 비우면 안 씀

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
        """test = 악기 시험 · compare = 비교 시험(첫 원곡 기준 60초, 같은 씨앗으로 모델만 바꿔 가며)."""
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
            ref = self._ref(work)
            for i, s in enumerate(srcs):
                self._make_one(i, len(srcs), s, work, out_dir, ref)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _moods(self, s: Source) -> list:
        if self.plan.moods:
            return list(self.plan.moods)
        if self.plan.analyze_on and s.analysis is not None:
            return list(s.analysis.moods)
        return ["calm"]

    def _ref(self, work: Path):
        """음색 참고 음원(있으면) → 엔진이 읽을 수 있는 곳(엔진 tmp)에 한 파일로."""
        files = [f for f in self.plan.refs if f and Path(f).is_file()][: prompt.MAX_REFS]
        if not files:
            return None
        self.step.emit("generate", 0.0, "음색 참고 음원 준비")
        return ff.make_ref(files, work / "ref.wav")

    def _meta(self, s: Source):
        """원곡에서 읽는 것 = 템포·조성(확실할 때만). 원곡 소리는 엔진에 보내지 않음."""
        p = self.plan
        a = s.analysis if p.analyze_on else None
        bpm = p.bpm if p.bpm_mode == "set" else (a.bpm if a is not None and a.bpm else None)
        key = a.key_scale if a is not None and a.key_conf >= 0.15 else ""
        return a, bpm, key

    def _make_one(self, i: int, n: int, s: Source, work: Path, out_dir: Path, ref):
        p = self.plan
        a, bpm, key = self._meta(s)
        sec = p.target_seconds(s)
        wd = work / f"{i + 1}"
        wd.mkdir(parents=True, exist_ok=True)
        moods = self._moods(s)
        req = prompt.request(instr_keys=p.instruments, mood_keys=moods, seconds=sec, bpm=bpm, key_scale=key,
                             ref_path=ref, candidates=p.candidates, profile=hw.profile())
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
            self.item.emit({"path": str(out), "source": s.name, "src_path": s.path, "instr": ilab, "n": k + 1,
                            "of": len(wavs), "seconds": sec})
        write_history(self._record(req, s, saved, moods, sec, a))
        self.step.emit("generate", (i + 1) / n, f"{label} 완료")

    def _record(self, req: dict, s: Source | None, saved: list, moods: list, sec: float, a=None, **more) -> dict:
        """작업 기록 한 줄: 엔진에 실제로 보낸 것 그대로."""
        rec = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "source": s.path if s is not None else "",
               "outputs": saved, "instruments": self.plan.instruments, "moods": moods, "seconds": sec,
               "bpm": req.get("bpm"), "key": req.get("key_scale"), "task": req.get("task_type"),
               "model": req.get("model"), "thinking": req.get("thinking"), "caption": req.get("prompt"),
               "lyrics": req.get("lyrics"), "negative": req.get("lm_negative_prompt"),
               "refs": list(self.plan.refs) if req.get("reference_audio_path") else [],
               "seed": req.get("seed"), "mode": hw.profile().mode,
               "analysis": a.to_dict() if a is not None else None}
        rec.update(more)
        return rec

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
                                     key_scale="C major", candidates=1, profile=hw.profile())
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
        """모델 비교(v0.1.8): 첫 원곡 기준 60초 · 같은 지시문 · 같은 씨앗으로 모델만 바꿔 가며(기본 / shift3 / XL).
        없는 모델은 먼저 내려받음. 다음 [AUTO] 는 요청에 기본 모델을 적으므로 서버가 스스로 되돌림."""
        p = self.plan
        srcs = [s for s in self.sources if s.checked and not s.error]
        if not srcs:
            raise EG.EngineError("비교할 원곡이 없습니다.")
        s = srcs[0]
        if s.analysis is None and p.analyze_on:
            s.analysis = analyze.analyze_file(s.path)
            s.duration = s.analysis.duration
        a, bpm, key = self._meta(s)
        sec = float(prompt.COMPARE_SECONDS)
        seed = random.randint(1, 2 ** 31 - 1)
        moods = self._moods(s)
        models = compare_models()
        n = len(models)
        work = EG.tmp_dir() / time.strftime("cmp_%Y%m%d_%H%M%S")
        work.mkdir(parents=True, exist_ok=True)
        ilab = prompt.instr_label(p.instruments)
        try:
            ref = self._ref(work)
            for i, (model, tag, name, _gb, _vram) in enumerate(models):
                if self.cancel:
                    raise EG.Cancelled("취소했습니다")
                if not EG.model_ready(model):
                    t1 = time.monotonic()

                    def dl(t, i=i, name=name, t1=t1):
                        self.step.emit("generate", i / n, f"{i + 1}/{n} {name} 내려받는 중 · {int(time.monotonic() - t1)}초 · {t[:50]}")
                    self.step.emit("generate", i / n, f"{i + 1}/{n} {name} 내려받는 중(처음 한 번)")
                    EG.download_model(model, dl, self.stopped)
                req = prompt.request(instr_keys=p.instruments, mood_keys=moods, seconds=sec, bpm=bpm, key_scale=key,
                                     ref_path=ref, candidates=1, profile=hw.profile(), seed=seed, model=model)
                t0 = time.monotonic()

                def prog(f, text, i=i, name=name, t0=t0):
                    self.step.emit("generate", (i + min(0.95, max(0.0, f))) / n,
                                   f"{i + 1}/{n} {name} · {int(time.monotonic() - t0)}초")
                self.step.emit("generate", i / n, f"{i + 1}/{n} {name} · 모델 읽는 중")
                wavs = self.eng.generate(req, work / f"{i + 1}", prog, self.stopped)
                if model == prompt.MAIN_MODEL:
                    remember_speed(sec, time.monotonic() - t0)
                stem = prompt.out_stem(p.title, s.name, p.instruments, tag, sec)
                out = ff.convert(wavs[0], unique(out_dir / (stem + ff.FORMATS[p.fmt][0])), p.fmt)
                self.outputs.append(out)
                self.item.emit({"path": str(out), "source": s.name, "src_path": s.path, "label": f"{i + 1}. {name}",
                                "instr": ilab, "seed": seed, "n": 1, "of": 1, "seconds": sec})
                write_history(self._record(req, s, [str(out)], moods, sec, a, kind="compare_model"))
                self.step.emit("generate", (i + 1) / n, f"{i + 1}/{n} {name} 완료")
        finally:
            shutil.rmtree(work, ignore_errors=True)


def compare_models() -> list:
    """이 컴퓨터에서 비교할 모델: 기본 · shift3 · (그래픽 메모리 20GB 이상이면) XL."""
    g = hw.gpu()
    vram = g.vram_gb if g is not None and hw.profile().mode == "gpu" else 0
    return [m for m in prompt.MODELS if m[4] <= vram or m[4] == 0]
