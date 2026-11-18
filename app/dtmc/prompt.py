"""악기·분위기·원곡 반영 정도 → ACE-Step 요청(caption·bpm·key_scale·task_type…).

원곡 반영 정도 = 0~100%(5% 단위, 기본 70%) — 슬라이더 하나(v0.1.6, A안):
- 0%: 원곡 소리를 엔진에 보내지 않음. 분석한 템포·조성·분위기만 글로 전달(text2music).
- 1~60%: 원곡을 목표 길이로 맞춘 참고 음원으로 커버(cover). audio_cover_strength = %/100
  (ACE-Step: 앞 int(단계 수 × 강도) 단계만 원곡 조건 — turbo 8단계라 실제로는 1/8 단위).
- 60% 초과: cover 강도는 계속 %/100, 여기에 cover_noise_strength 를 0 → NOISE_MAX 로 서서히(원곡 잠재값에서 출발).
  (잠정 — [비교 시험] 결과로 조정)
- 반영 > 0 이면 bpm·key 를 보내지 않음(원곡 그대로 따라가게).
"""
from __future__ import annotations

# (키, 화면 이름, 지시문 단어)
INSTRUMENTS = [
    ("piano", "피아노", "piano"),
    ("strings", "스트링", "string ensemble"),
    ("guitar", "어쿠스틱 기타", "acoustic guitar"),
    ("harp", "하프", "harp"),
    ("cello", "첼로", "cello"),
    ("violin", "바이올린", "violin"),
    ("flute", "플루트", "flute"),
    ("orchestra", "오케스트라", "full orchestra"),
]
INSTR = {k: (ko, en) for k, ko, en in INSTRUMENTS}
MAX_INSTR = 3

MOODS = [
    ("calm", "차분함", "calm, peaceful"),
    ("warm", "따뜻함", "warm, intimate"),
    ("bright", "밝음", "bright, uplifting"),
    ("sad", "서정적", "melancholic, emotional"),
    ("energetic", "경쾌함", "energetic, lively"),
    ("epic", "웅장함", "cinematic, epic"),
    ("dreamy", "몽환적", "dreamy, ambient"),
]
MOOD = {k: (ko, en) for k, ko, en in MOODS}

DEFAULT_STRENGTH = 70
WARN_STRENGTH = 80                        # 이보다 크면 노란 경고
NOISE_FROM, NOISE_MAX = 60, 0.6           # A안: 60% 초과부터 cover_noise_strength 0 → 0.6(100%)
COMPARE_STRENGTHS = (30, 50, 70, 90)
COMPARE_SECONDS = 30
_OLD_STRENGTH = {"low": 0, "mid": 30, "high": 60}     # v0.1.5 이하 설정값


def strength_pct(v) -> int:
    """설정값 → 0~100(5% 단위). 옛 low/mid/high = 0/30/60."""
    if isinstance(v, str) and v in _OLD_STRENGTH:
        return _OLD_STRENGTH[v]
    try:
        x = float(v)
    except (TypeError, ValueError):
        return DEFAULT_STRENGTH
    return int(min(100, max(0, round(x / 5) * 5)))


def cover_params(pct) -> tuple[float, float] | None:
    """반영 % → (audio_cover_strength, cover_noise_strength). 0% = None(text2music)."""
    v = strength_pct(pct)
    if v <= 0:
        return None
    noise = 0.0 if v <= NOISE_FROM else NOISE_MAX * (v - NOISE_FROM) / (100 - NOISE_FROM)
    return round(v / 100, 3), round(noise, 3)

LEN_MIN, LEN_MAX = 10, 600


def instr_label(keys) -> str:
    return "+".join(INSTR[k][0] for k in keys if k in INSTR) or "피아노"


def caption(instr_keys, mood_keys) -> str:
    ks = [k for k in instr_keys if k in INSTR] or ["piano"]
    names = [INSTR[k][1] for k in ks]
    if len(names) == 1:
        lead = ("solo " + names[0]) if ks[0] in ("piano", "guitar", "harp", "cello", "violin", "flute") else names[0]
    else:
        lead = ", ".join(names[:-1]) + " and " + names[-1]
    only = (" and ".join(names) if len(names) <= 2 else ", ".join(names)) + " only"
    moods = [MOOD[m][1] for m in mood_keys if m in MOOD]
    # 고른 악기만(v0.1.6): DiT 에는 부정 지시문이 따로 없어 글 안에 '만 · 금지'를 넣음
    ban = "no other instruments, no drums, no percussion, no bass, no synth, no vocals"
    parts = [lead, "instrumental", only] + moods + ["acoustic performance", "high quality recording", ban]
    return ", ".join(parts)


def clamp_len(sec) -> float:
    return float(min(LEN_MAX, max(LEN_MIN, round(float(sec)))))


def request(*, instr_keys, mood_keys, seconds, bpm=None, key_scale="", strength=0, src_path=None,
            candidates=1, profile=None, seed=None) -> dict:
    """ACE-Step /release_task JSON. src_path = 서버가 읽을 참고 음원(같은 컴퓨터의 절대 경로).
    strength = 원곡 반영 %(옛 low/mid/high 도 받음). 반영 > 0 이면 bpm·key 는 무시(원곡 그대로)."""
    cp = cover_params(strength) if src_path else None
    if cp is not None:
        bpm, key_scale = None, ""
    req = {
        "prompt": caption(instr_keys, mood_keys),
        "lyrics": "[Instrumental]",
        "vocal_language": "en",
        "audio_duration": clamp_len(seconds),
        "batch_size": max(1, min(4, int(candidates))),
        "audio_format": "wav",
        "inference_steps": 8,
        "use_cot_caption": False,             # 지시문을 언어 모델이 바꾸지 않게(악기 지정 유지)
        "use_cot_language": False,
        "time_signature": "4",
    }
    if bpm:
        req["bpm"] = int(round(min(300, max(30, float(bpm)))))
    if key_scale:
        req["key_scale"] = key_scale
    if seed is not None:
        req["use_random_seed"] = False
        req["seed"] = int(seed)
    if cp is not None:
        req["task_type"] = "cover"
        req["src_audio_path"] = str(src_path)
        req["audio_cover_strength"] = cp[0]
        req["cover_noise_strength"] = cp[1]
    else:
        req["task_type"] = "text2music"
        req["thinking"] = bool(profile.thinking and profile.lm_ready) if profile is not None else False
    return req


# ------------------------------------------------------------ 결과 파일 이름(v0.1.6)
import re as _re  # noqa: E402

_BAD = _re.compile(r'[\\/:*?"<>|]+')


def safe(name: str) -> str:
    return _BAD.sub("_", name).strip().rstrip(".") or "곡"


def first_word(name: str) -> str:
    """원곡 이름의 첫 단어(공백·_·- 로 나눔, 트랙 번호처럼 숫자·기호뿐인 조각은 건너뜀)."""
    toks = [t for t in _re.split(r"[\s_\-.]+", name or "") if t]
    for t in toks:
        if any(ch.isalpha() for ch in t):
            return safe(t)
    return safe(toks[0]) if toks else "곡"


def len_tag(sec) -> str:
    """길이 → 30s · 1m30s · 2m (콜론 없이)."""
    sec = int(round(float(sec or 0)))
    if sec < 60:
        return f"{sec}s"
    m, s_ = divmod(sec, 60)
    return f"{m}m{s_}s" if s_ else f"{m}m"


def out_stem(title: str, source_name: str, instr_keys, tag, seconds) -> str:
    """[제목]_[악기]_[번호 또는 반영NN]_[길이]. 제목이 비면 원곡 이름 첫 단어."""
    t = safe(title.strip()) if title and title.strip() else first_word(source_name)
    return f"{t}_{instr_label(instr_keys)}_{tag}_{len_tag(seconds)}"
