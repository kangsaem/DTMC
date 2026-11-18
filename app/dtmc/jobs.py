"""작업(백그라운드 스레드): 원곡 분석 · 생성(엔진 준비 → 원곡마다 생성 → 형식 변환·저장) · 악기 시험 · 비교 시험."""
from __future__ import annotations

import dataclasses
import json
import random
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from . import analyze, engine as EG, ff, fit, hw, log, paths, prompt, settings

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
    quality: str = "std"                             # 늘 std(shift3 + 큰 GPU 면 작곡 4B + 대체 변환부) — XL 선택은 v0.1.12 에서 뺌
    style: str = prompt.DEFAULT_STYLE                # 스타일 키(prompt.STYLES)
    caption: str = ""                                # 직접 고친 지시문(비면 스타일 지시문)
    src_feel: bool = False                           # 원곡 느낌 참고 = 원곡 소리를 음색 참고로(멜로디는 안 따라감)
    refs: list = field(default_factory=list)         # 음색 참고 음원(실제 악기 녹음, 최대 3개) — 비우면 안 씀
    cpu_lm: str = "off"                              # CPU(노트북) 작곡 담당: off · 0.6B · 1.7B(v0.1.14) — GPU 에서는 안 씀

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
def mode_key(cpu_lm: str = "off") -> str:
    """속도 기억 이름: gpu · cpu · cpu_0.6B · cpu_1.7B(작곡 담당을 켜면 속도가 달라서 따로)."""
    m = hw.profile().mode
    return f"{m}_{cpu_lm}" if m == "cpu" and cpu_lm in prompt.CPU_LM_MODEL else m


def speed(lm: str = "off") -> float | None:
    """오디오 1초를 만드는 데 걸린 시간(초) — 지난 생성에서 잰 값. 없으면 None."""
    v = settings.get("speed_" + mode_key(lm))
    return float(v) if v else None


def remember_speed(audio_sec: float, took: float, lm: str = "off") -> None:
    if audio_sec <= 0:
        return
    s = took / audio_sec
    old = speed(lm)
    settings.put("speed_" + mode_key(lm), round(s if old is None else old * 0.5 + s * 0.5, 3))


def estimate(total_audio_sec: float, lm: str = "off") -> float | None:
    s = speed(lm)
    if s is None:
        return None
    start = settings.get("start_" + mode_key(lm)) or 60.0
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
                 compare: bool = False, fit_len: bool = False, vocal: bool = False, parent=None):
        """test = 악기 시험 · compare = 비교 시험(첫 원곡 기준 60초, 같은 씨앗으로 모델만 바꿔 가며)."""
        super().__init__(parent)
        self.eng, self.sources, self.plan, self.test, self.compare = eng, list(sources), plan, test, compare
        self.vocal = vocal                         # 보컬 시험(v0.1.16): 애니 오프닝 30초 × 가사 칸 · 지시문 4가지
        self.fit_len = fit_len                     # 원곡 길이 맞추기 시험(편집만 / 이음매 다시 그리기 / 커버 2가지)
        self.cancel = False
        self.failed: list[str] = []                # 비교 시험에서 건너뛴 곡(이유)
        self.outputs: list[Path] = []
        self.prof = hw.profile()                   # 이번 작업의 실행 모드(CPU 에서 작곡 담당을 켜면 바뀜)
        self.lm_key = "off"                        # 실제로 켠 CPU 작곡 담당(속도 기억용)

    def stopped(self) -> bool:
        return self.cancel

    def steps(self) -> list[str]:
        if self.test or self.compare or self.fit_len or self.vocal:
            return ["engine", "generate"]
        return ["engine", "analyze", "generate"] if self.plan.analyze_on else ["engine", "generate"]

    def run(self):
        t_all = time.monotonic()
        try:
            self._run()
            n = len(self.outputs)
            took = time.monotonic() - t_all
            self.finished_ok.emit(True, f"{n}곡 저장 · {int(took // 60)}분 {int(took % 60)}초"
                                  + (f" · 작곡 담당 {self.lm_key}" if self.lm_key != "off" else "")
                                  + (f" · {len(self.failed)}곡 실패(dtmc.log)" if self.failed else ""))
        except EG.Cancelled:
            self.finished_ok.emit(False, "취소했습니다")
        except Exception as e:  # noqa: BLE001
            L.exception("생성 작업 실패")
            self.finished_ok.emit(False, str(e))

    def _engine(self):
        """필요한 모델을 받고(처음 한 번) 품질 선택에 맞는 환경으로 엔진을 켬. 환경이 지금 서버와 다르면 다시 켬."""
        ps = self.ps = prompt.preset(self.plan.quality, xl_ok())
        senv = dict(ps["env"])
        self._need(ps["model"], key="engine")
        lm = senv.get("ACESTEP_LM_MODEL_PATH")
        if lm:
            try:
                self._need(lm, key="engine")
            except EG.Cancelled:
                raise
            except Exception as e:  # noqa: BLE001 — 큰 작곡 담당을 못 받으면 평소 것으로
                L.warning("작곡 담당 %s 준비 실패 → 평소 것으로: %s", lm, e)
                senv.pop("ACESTEP_LM_MODEL_PATH", None)
        cpu_lm = self._cpu_lm(senv)
        self.step.emit("engine", 0.0, "엔진 켜는 중(처음에는 모델을 읽느라 몇 분 걸릴 수 있음)")
        t0 = time.monotonic()
        was = self.eng.running() and tuple(sorted(senv.items())) == getattr(self.eng, "env_key", ())

        def line(t):
            if '"GET /' in t or '"POST /' in t or "HTTP/1.1" in t:     # 서버 접속 기록은 숨김(엔진 로그에는 남음)
                return
            self.step.emit("engine", 0.0, t[:120])
        try:
            self.eng.start(line, self.stopped, extra_env=senv)
        except EG.Cancelled:
            raise
        except EG.EngineError as e:
            if cpu_lm != "off":
                # CPU 작곡 담당 때문에 서버가 못 켜졌을 수 있음(메모리 부족 등) → 작곡 담당 없이 한 번 더
                L.warning("작곡 담당 %s 로 켜기 실패 → 끈 채로 다시: %s", cpu_lm, e)
                senv.pop("ACESTEP_INIT_LLM", None)
                senv.pop("ACESTEP_LM_MODEL_PATH", None)
                cpu_lm = "off"
                self.step.emit("engine", 0.0, "작곡 담당 없이 다시 켜는 중(메모리 부족일 수 있음 · engine.log)")
                self._start_vae_fallback(line, senv)
            elif "ACESTEP_VAE_CHECKPOINT" in senv:
                self._vae_retry(line, senv, e)
            else:
                raise
        self.senv = senv
        self._lm_result(cpu_lm)
        if ps["quality"] == "hq" and not (EG.external_url() or self.eng.dcw):
            raise EG.EngineError("고품질(XL)은 DCW 를 꺼야 하는데 엔진을 고치지 못했습니다(dtmc.log). 기본 품질로 만드세요.")
        if not was:
            settings.put("start_" + mode_key(self.lm_key), round(time.monotonic() - t0, 1))
        self.step.emit("engine", 1.0, "엔진 준비됨" + (f" · 작곡 담당 {self.lm_key}" if self.lm_key != "off" else ""))

    def _start_vae_fallback(self, line, senv: dict) -> None:
        try:
            self.eng.start(line, self.stopped, extra_env=senv)
        except EG.Cancelled:
            raise
        except EG.EngineError as e:
            if "ACESTEP_VAE_CHECKPOINT" not in senv:
                raise
            self._vae_retry(line, senv, e)

    def _vae_retry(self, line, senv: dict, e) -> None:
        # 대체 음원 변환부를 못 받았을 수 있음(인터넷 없음 등) → 공식 변환부로 한 번 더
        L.warning("대체 음원 변환부로 켜기 실패 → 공식 변환부로 다시: %s", e)
        senv.pop("ACESTEP_VAE_CHECKPOINT", None)
        self.step.emit("engine", 0.0, "대체 음원 변환부 없이 다시 켜는 중")
        self.eng.start(line, self.stopped, extra_env=senv)

    def _cpu_lm(self, senv: dict) -> str:
        """CPU(노트북)에서 작곡 담당을 고른 경우: 모델 준비(0.6B 는 처음 받음) + 서버 환경. 돌려줌 = 켜려는 것(off · 0.6B · 1.7B)."""
        k = self.plan.cpu_lm
        if hw.profile().mode != "cpu" or k not in prompt.CPU_LM_MODEL:
            return "off"
        lm = prompt.CPU_LM_MODEL[k]
        try:
            self._need(lm, key="engine")
        except EG.Cancelled:
            raise
        except Exception as e:  # noqa: BLE001 — 못 받으면 끈 채로
            L.warning("작곡 담당 %s 내려받기 실패 → 끈 채로: %s", lm, e)
            self.step.emit("engine", 0.0, f"작곡 담당 {k} 를 받지 못해 끈 채로 만듭니다")
            return "off"
        senv["ACESTEP_INIT_LLM"] = "true"           # CPU 기본 = 끔(_server_env) → 여기서 덮어씀
        senv["ACESTEP_LM_MODEL_PATH"] = lm
        return k

    def _lm_result(self, k: str) -> None:
        """서버가 작곡 담당을 실제로 읽었는지 로그("LLM model loaded:")로 확인 → 이번 작업의 thinking 켬/끔."""
        self.prof, self.lm_key = hw.profile(), "off"
        if k == "off":
            return
        lm = prompt.CPU_LM_MODEL[k]
        got = self.eng.lm_loaded
        if EG.external_url() or (got and lm in got):
            self.prof = dataclasses.replace(hw.profile(), init_llm=True, lm_model=lm, thinking=True)
            self.lm_key = k
            L.info("CPU 작곡 담당 켬: %s", got or lm)
        else:
            L.warning("CPU 작곡 담당 %s 를 켜지 못함(서버 로그: %s) → 끈 채로", lm, got or "읽지 못함")
            self.step.emit("engine", 0.0, f"작곡 담당 {k} 를 켜지 못해 끈 채로 만듭니다(메모리 부족일 수 있음 · engine.log)")

    def _need(self, model: str, frac: float = 0.0, head: str = "", key: str = "generate"):
        """모델이 없으면 내려받음(처음 한 번). 진행 글 = key 단계 막대에."""
        if EG.model_ready(model):
            return
        name = prompt.MODEL_INFO.get(model, (model, 0))[0]
        t1 = time.monotonic()

        def dl(t):
            self.step.emit(key, frac, f"{head}{name} 내려받는 중 · {int(time.monotonic() - t1)}초 · {t[:50]}")
        self.step.emit(key, frac, f"{head}{name} 내려받는 중(처음 한 번)")
        EG.download_model(model, dl, self.stopped)

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
        if self.fit_len:
            return self._run_fit(out_dir)
        if self.vocal:
            return self._run_vocal(out_dir)
        srcs = [s for s in self.sources if s.checked and not s.error] or [None]      # 원곡은 선택 사항(없으면 스타일만으로)
        if p.analyze_on:
            real = [s for s in srcs if s is not None]
            for i, s in enumerate(real):
                if self.cancel:
                    raise EG.Cancelled("취소했습니다")
                self.step.emit("analyze", i / len(real), s.name)
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

    def _ref(self, work: Path, s: Source | None = None):
        """음색 참고 음원 → 엔진이 읽을 수 있는 곳(엔진 tmp)에 한 파일로.
        원곡 느낌 참고를 켰고 원곡이 있으면 그 원곡, 아니면 '음색 참고' 칸의 파일들(없으면 None)."""
        p = self.plan
        if p.src_feel and p.analyze_on and s is not None:
            files = [s.path]
        else:
            files = [f for f in p.refs if f and Path(f).is_file()][: prompt.MAX_REFS]
        if not files:
            return None
        self.step.emit("generate", 0.0, "음색 참고 음원 준비")
        work.mkdir(parents=True, exist_ok=True)
        return ff.make_ref(files, work / "ref.wav")

    def _meta(self, s: Source | None):
        """원곡에서 읽는 것 = 템포·조성(확실할 때만). 원곡 소리는 '원곡 느낌 참고'를 켰을 때만 음색 참고로.
        돌려주는 것: 분석 · 템포 · 조성 · 템포를 직접 정했는지."""
        p = self.plan
        a = s.analysis if (p.analyze_on and s is not None) else None
        if p.bpm_mode == "set":
            return a, p.bpm, (a.key_scale if a is not None and a.key_conf >= 0.15 else ""), True
        bpm = a.bpm if a is not None and a.bpm else None
        key = a.key_scale if a is not None and a.key_conf >= 0.15 else ""
        return a, bpm, key, False

    def _make_one(self, i: int, n: int, s: Source | None, work: Path, out_dir: Path):
        p = self.plan
        a, bpm, key, forced = self._meta(s)
        sec = p.target_seconds(s)
        wd = work / f"{i + 1}"
        wd.mkdir(parents=True, exist_ok=True)
        st = prompt.STYLE_BY.get(p.style, prompt.STYLE_BY[prompt.DEFAULT_STYLE])
        ref = self._ref(wd, s)
        req = prompt.style_request(st, seconds=sec, bpm=bpm, key_scale=key, ref_path=ref, candidates=p.candidates,
                                   profile=self.prof, model=self.ps["model"], caption=p.caption, force_bpm=forced,
                                   **self.ps["opt"])
        name = s.name if s is not None else st[1]
        label = f"{i + 1}/{n} {name}"
        t0 = time.monotonic()

        def prog(f, text):
            el = time.monotonic() - t0
            self.step.emit("generate", (i + min(0.95, max(0.0, f))) / n, f"{label} · {int(el)}초" + (f" · {text[:60]}" if text else ""))
        wavs = self.eng.generate(req, wd, prog, self.stopped)
        remember_speed(sec * len(wavs), time.monotonic() - t0, self.lm_key)
        self.step.emit("generate", (i + 0.97) / n, f"{label} · 저장")
        slab = st[1] + (" · 직접 고침" if (p.caption or "").strip() and p.caption.strip() != st[2] else "")
        saved = []
        for k, w in enumerate(wavs):
            stem = prompt.stem(p.title, s.name if s is not None else "", st[1], k + 1, sec)
            dst = unique(out_dir / (stem + ff.FORMATS[p.fmt][0]))
            out = ff.convert(w, dst, p.fmt)
            self.outputs.append(out)
            saved.append(str(out))
            self.item.emit({"path": str(out), "source": s.name if s is not None else "", "label": slab,
                            "src_path": s.path if s is not None else "", "instr": "", "n": k + 1,
                            "of": len(wavs), "seconds": sec})
        write_history(self._record(req, s, saved, [], sec, a, style=st[0], src_feel=bool(p.src_feel and s is not None)))
        self.step.emit("generate", (i + 1) / n, f"{label} 완료")

    def _record(self, req: dict, s: Source | None, saved: list, moods: list, sec: float, a=None, **more) -> dict:
        """작업 기록 한 줄: 엔진에 실제로 보낸 것 그대로."""
        rec = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "source": s.path if s is not None else "",
               "outputs": saved, "instruments": self.plan.instruments, "moods": moods, "seconds": sec,
               "bpm": req.get("bpm"), "key": req.get("key_scale"), "task": req.get("task_type"),
               "model": req.get("model"), "thinking": req.get("thinking"), "caption": req.get("prompt"),
               "lyrics": req.get("lyrics"), "negative": req.get("lm_negative_prompt"),
               "refs": (list(self.plan.refs) if req.get("reference_audio_path") else []),
               "seed": req.get("seed"), "mode": hw.profile().mode, "cpu_lm": self.lm_key, "quality": getattr(self, "ps", {}).get("quality"),
               "server_env": getattr(self, "senv", {}), "lm": getattr(self.eng, "lm_loaded", ""),
               "dcw_enabled": req.get("dcw_enabled"),
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
                                     key_scale="C major", candidates=1, profile=self.prof, model=self.ps["model"],
                                     **self.ps["opt"])
                t0 = time.monotonic()

                def prog(f, text, i=i, ko=ko, t0=t0):
                    self.step.emit("generate", (i + min(0.95, f)) / len(keys),
                                   f"{i + 1}/{len(keys)} {ko} · {int(time.monotonic() - t0)}초")
                wavs = self.eng.generate(req, work / k, prog, self.stopped)
                remember_speed(TEST_SECONDS, time.monotonic() - t0, self.lm_key)
                out = ff.convert(wavs[0], d / f"{i + 1:02d}_{ko}{ff.FORMATS[self.plan.fmt][0]}", self.plan.fmt)
                self.outputs.append(out)
                self.item.emit({"path": str(out), "source": "악기 시험", "instr": ko, "n": 1, "of": 1,
                                "seconds": TEST_SECONDS})
                write_history({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "source": "악기 시험", "outputs": [str(out)],
                               "instruments": [k], "caption": req["prompt"], "mode": hw.profile().mode})
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _run_compare(self, out_dir: Path):
        """스타일 샘플: 60초 · 같은 씨앗으로 prompt.STYLES 를 한 곡씩(각 스타일의 기본 지시문).
        첫 원곡이 있으면 조성과(스타일 범위 안일 때만) 템포를 가져옴 — 원곡 없이도 됨. 한 곡이 실패해도 나머지는 계속."""
        p = self.plan
        srcs = [s for s in self.sources if s.checked and not s.error]
        s = srcs[0] if srcs else None
        if s is not None and s.analysis is None and p.analyze_on:
            s.analysis = analyze.analyze_file(s.path)
            s.duration = s.analysis.duration
        a, bpm, key, forced = self._meta(s)
        sec = float(prompt.COMPARE_SECONDS)
        seed = random.randint(1, 2 ** 31 - 1)
        styles = prompt.STYLES
        n = len(styles)
        work = EG.tmp_dir() / time.strftime("cmp_%Y%m%d_%H%M%S")
        work.mkdir(parents=True, exist_ok=True)
        failed = []
        try:
            ref = self._ref(work, s)
            for i, st in enumerate(styles):
                if self.cancel:
                    raise EG.Cancelled("취소했습니다")
                ko = st[1]
                head = f"{i + 1}/{n} "
                req = prompt.style_request(st, seconds=sec, bpm=bpm, key_scale=key, ref_path=ref, profile=self.prof,
                                           seed=seed, model=self.ps["model"], force_bpm=forced, **self.ps["opt"])
                t0 = time.monotonic()

                def prog(f, text, i=i, ko=ko, t0=t0):
                    self.step.emit("generate", (i + min(0.95, max(0.0, f))) / n,
                                   f"{i + 1}/{n} {ko} · {int(time.monotonic() - t0)}초")
                self.step.emit("generate", i / n, f"{head}{ko}")
                try:
                    wavs = self.eng.generate(req, work / f"{i + 1}", prog, self.stopped)
                except EG.Cancelled:
                    raise
                except Exception as e:  # noqa: BLE001 — 이 곡만 건너뜀
                    L.warning("스타일 샘플 %s 실패: %s", ko, e)
                    failed.append(f"{ko}: {e}")
                    self.step.emit("generate", (i + 1) / n, f"{head}{ko} 실패 — 건너뜀")
                    if not self.eng.running() and not EG.external_url():      # 엔진이 죽었으면 다시 켬
                        self.eng.start(lambda t: None, self.stopped, extra_env=self.senv)
                    continue
                remember_speed(sec, time.monotonic() - t0, self.lm_key)
                stem = prompt.style_stem(p.title, s.name if s is not None else "", i + 1, ko, sec)
                out = ff.convert(wavs[0], unique(out_dir / (stem + ff.FORMATS[p.fmt][0])), p.fmt)
                self.outputs.append(out)
                self.item.emit({"path": str(out), "source": s.name if s is not None else "",
                                "src_path": s.path if s is not None else "", "label": f"{i + 1}. {ko}",
                                "instr": "", "seed": seed, "n": 1, "of": 1, "seconds": sec})
                write_history(self._record(req, s, [str(out)], [], sec, a, kind="style_sample", style=st[0]))
                self.step.emit("generate", (i + 1) / n, f"{head}{ko} 완료")
            if failed and not self.outputs:
                raise EG.EngineError("스타일 샘플이 모두 실패했습니다: " + " / ".join(failed)[:300])
            self.failed = failed
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _run_vocal(self, out_dir: Path):
        """보컬 시험: 애니 오프닝 30초 · 같은 씨앗 · 작곡 담당은 고른 설정 그대로. 가사 칸 · 지시문만 바꿔 4곡.
        원곡이 있으면 조성 · 템포(스타일 범위 안일 때만)를 가져옴. 한 곡이 실패해도 나머지는 계속."""
        p = self.plan
        srcs = [s for s in self.sources if s.checked and not s.error]
        s = srcs[0] if srcs else None
        if s is not None and s.analysis is None and p.analyze_on:
            s.analysis = analyze.analyze_file(s.path)
            s.duration = s.analysis.duration
        a, bpm, key, forced = self._meta(s)
        sec = float(prompt.VOCAL_SECONDS)
        seed = random.randint(1, 2 ** 31 - 1)
        st = prompt.STYLE_BY["anime"]
        cases = prompt.vocal_cases("anime")
        n = len(cases)
        work = EG.tmp_dir() / time.strftime("voc_%Y%m%d_%H%M%S")
        work.mkdir(parents=True, exist_ok=True)
        failed = []
        try:
            for i, (no, name, cap, lyr) in enumerate(cases):
                if self.cancel:
                    raise EG.Cancelled("취소했습니다")
                head = f"{i + 1}/{n} "
                req = prompt.style_request(st, seconds=sec, bpm=bpm, key_scale=key, profile=self.prof, seed=seed,
                                           model=self.ps["model"], caption=cap, force_bpm=forced, **self.ps["opt"])
                req["lyrics"] = lyr
                t0 = time.monotonic()

                def prog(f, text, i=i, name=name, t0=t0):
                    self.step.emit("generate", (i + min(0.95, max(0.0, f))) / n,
                                   f"{i + 1}/{n} {name} · {int(time.monotonic() - t0)}초")
                self.step.emit("generate", i / n, f"{head}{name}")
                try:
                    wavs = self.eng.generate(req, work / f"{no}", prog, self.stopped)
                except EG.Cancelled:
                    raise
                except Exception as e:  # noqa: BLE001 — 이 곡만 건너뜀
                    L.warning("보컬 시험 %s 실패: %s", name, e)
                    failed.append(f"{name}: {e}")
                    self.step.emit("generate", (i + 1) / n, f"{head}{name} 실패 — 건너뜀")
                    if not self.eng.running() and not EG.external_url():
                        self.eng.start(lambda t: None, self.stopped, extra_env=self.senv)
                    continue
                remember_speed(sec, time.monotonic() - t0, self.lm_key)
                stem = prompt.style_stem(p.title, s.name if s is not None else "보컬시험", no, f"애니_{name}", sec)
                out = ff.convert(wavs[0], unique(out_dir / (stem + ff.FORMATS[p.fmt][0])), p.fmt)
                self.outputs.append(out)
                self.item.emit({"path": str(out), "source": s.name if s is not None else "",
                                "src_path": s.path if s is not None else "", "label": f"{no}. 애니 · {name}",
                                "instr": "", "seed": seed, "n": 1, "of": 1, "seconds": sec})
                write_history(self._record(req, s, [str(out)], [], sec, a, kind="vocal_test", style=st[0], case=name))
                self.step.emit("generate", (i + 1) / n, f"{head}{name} 완료")
            if failed and not self.outputs:
                raise EG.EngineError("보컬 시험이 모두 실패했습니다: " + " / ".join(failed)[:300])
            self.failed = failed
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _run_fit(self, out_dir: Path):
        """원곡 길이 맞추기 시험(v0.1.13): 첫 원곡을 plan.seconds 길이로 재배치한 뒤 4가지로.
        1 편집만(엔진 안 씀) · 2 이음매 다시 그리기(repaint — 이음매 앞뒤만 새로, 나머지는 원곡 소리) ·
        3 커버 100%(원곡 스타일 — 전체를 다시 연주) · 4 커버 70%(고른 스타일). 원곡 자체를 쓰는 결과임.
        한 곡이 실패해도 나머지는 계속."""
        p = self.plan
        srcs = [s for s in self.sources if s.checked and not s.error]
        if not srcs:
            raise EG.EngineError("길이를 맞출 원곡이 없습니다.")
        s = srcs[0]
        if s.analysis is None:
            s.analysis = analyze.analyze_file(s.path)
            s.duration = s.analysis.duration
        target = prompt.clamp_len(p.seconds)
        self.step.emit("generate", 0.0, f"{s.name} · 반복 구간 찾는 중")
        f = fit.plan_file(s.path, s.analysis.bpm, target)
        L.info("길이 맞추기: %s → %.1f초(목표 %.0f) · %s · 구간 %s · 이음매 %s", s.name, f.length, target, f.note,
               f.segments, f.seams)
        if not f.changed():
            raise EG.EngineError(f"원곡 길이({int(s.duration)}초)와 목표 길이({int(target)}초)가 거의 같아 바꿀 것이 없습니다. "
                                 "'길이'를 직접으로 두고 다른 값을 넣으세요.")
        work = EG.tmp_dir() / time.strftime("fit_%Y%m%d_%H%M%S")
        work.mkdir(parents=True, exist_ok=True)
        st = prompt.STYLE_BY.get(p.style, prompt.STYLE_BY[prompt.DEFAULT_STYLE])
        model = self.ps["model"]
        failed = []
        n = 4
        sec = f.length

        def save(i, tag, label, wav, req=None, **more):
            stem = prompt.stem(p.title, s.name, f"길이맞춤{i + 1}", tag, sec)
            out = ff.convert(wav, unique(out_dir / (stem + ff.FORMATS[p.fmt][0])), p.fmt)
            self.outputs.append(out)
            self.item.emit({"path": str(out), "source": s.name, "src_path": s.path, "label": f"{i + 1}. {label}",
                            "instr": f.note, "n": 1, "of": 1, "seconds": sec})
            rec = self._record(req or {}, s, [str(out)], [], sec, s.analysis, kind="fit_length", case=tag,
                               fit={"segments": f.segments, "seams": f.seams, "note": f.note, "target": target})
            rec.update(more)
            write_history(rec)
            self.step.emit("generate", (i + 1) / n, f"{i + 1}/{n} {label} 완료")

        def gen(i, label, req, sub):
            t0 = time.monotonic()

            def prog(fr, text):
                self.step.emit("generate", (i + min(0.95, max(0.0, fr))) / n,
                               f"{i + 1}/{n} {label} · {int(time.monotonic() - t0)}초")
            self.step.emit("generate", i / n, f"{i + 1}/{n} {label}")
            return self.eng.generate(req, work / sub, prog, self.stopped)[0]
        try:
            # 1. 편집만
            self.step.emit("generate", 0.0, f"1/{n} 편집만 · {f.note}")
            arr = ff.arrange(s.path, f.segments, work / "arranged.wav")
            save(0, "편집만", "편집만(엔진 안 씀)", arr)
            # 원곡 묘사(2·3번 지시문) — 작곡 담당이 있을 때만, 못 하면 기본 글
            cap = ""
            if self.prof.thinking and self.prof.lm_ready:
                try:
                    self.step.emit("generate", 1 / n, "원곡 듣고 묘사 쓰는 중")
                    usrc = ff.make_source(arr, work / "und.wav", min(120.0, max(10.0, sec)))
                    cap = (self.eng.understand(prompt.understand_request(usrc), lambda a, b: None, self.stopped)
                           .get("caption") or "").strip()
                except EG.Cancelled:
                    raise
                except Exception as e:  # noqa: BLE001
                    L.warning("원곡 묘사 실패(기본 글로): %s", e)
            # 2. 이음매 다시 그리기(이음매마다 차례로)
            try:
                cur, req2 = arr, {}
                wins = fit.repaint_windows(f)
                if not wins:
                    raise EG.EngineError("다시 그릴 이음매가 없습니다")
                for q, (a, b) in enumerate(wins):
                    if self.cancel:
                        raise EG.Cancelled("취소했습니다")
                    req2 = prompt.repaint_request(cur, a, b, cap, model)
                    cur = gen(1, f"이음매 다시 그리기 {q + 1}/{len(wins)}", req2, f"rp{q + 1}")
                save(1, "이음매", "이음매 다시 그리기", cur, req2, repaint=wins, understand=cap)
            except EG.Cancelled:
                raise
            except Exception as e:  # noqa: BLE001
                L.warning("이음매 다시 그리기 실패: %s", e)
                failed.append(f"이음매: {e}")
            # 3. 커버 100%(원곡 스타일) · 4. 커버 70%(고른 스타일)
            for i, tag, label, c, strength, noise in (
                    (2, "커버100", "커버 100%(원곡 스타일)", cap, 1.0, 0.6),
                    (3, f"커버70_{st[1]}", f"커버 70%({st[1]})", (p.caption or "").strip() or st[2], 0.7, 0.15)):
                if self.cancel:
                    raise EG.Cancelled("취소했습니다")
                try:
                    req = prompt.cover_request(arr, c, strength, noise, model)
                    save(i, tag, label, gen(i, label, req, f"cv{i}"), req, understand=cap if i == 2 else "")
                except EG.Cancelled:
                    raise
                except Exception as e:  # noqa: BLE001
                    L.warning("%s 실패: %s", label, e)
                    failed.append(f"{label}: {e}")
                    if not self.eng.running() and not EG.external_url():
                        self.eng.start(lambda t: None, self.stopped, extra_env=self.senv)
            self.failed = failed
        finally:
            shutil.rmtree(work, ignore_errors=True)


def xl_ok() -> bool:
    """XL 모델을 쓸 수 있는 컴퓨터인지(NVIDIA 그래픽 메모리 20GB 이상)."""
    g = hw.gpu()
    return bool(g is not None and hw.profile().mode == "gpu" and g.vram_gb >= prompt.XL_VRAM)


def needed_models(quality: str) -> list:
    """이 품질로 만드는 데 필요한데 아직 없는 모델."""
    ps = prompt.preset(quality, xl_ok())
    ms = [ps["model"]] + ([ps["env"]["ACESTEP_LM_MODEL_PATH"]] if "ACESTEP_LM_MODEL_PATH" in ps["env"] else [])
    return [m for m in ms if not EG.model_ready(m)]
