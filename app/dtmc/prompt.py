"""악기·분위기 → ACE-Step 요청(지시문 caption · 가사 칸 구조 태그 · 금지어 · bpm · key_scale …).

v0.1.8: 원곡은 '분위기 읽기'에만 쓴다(템포·조성·분위기). 원곡 소리는 엔진에 보내지 않는다(text2music).
커버(원곡 반영 %)는 목표(새 멜로디)와 맞지 않아 삭제 — HANDOFF '방향' 참고.
- 지시문 = 장르 + 묘사 문장(연주·음색·녹음) + 분위기 최대 2개. 템포·조성은 전용 칸(bpm · key_scale).
- 금지어는 지시문이 아니라 작곡 담당(LM) 전용 칸(lm_negative_prompt).
- 음색 참고(reference_audio_path, 선택) = 잘 녹음된 실제 악기 연주 → 음색·연주·녹음 질감만.
"""
from __future__ import annotations

import re as _re

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

MAX_MOODS = 2                             # 지시문에 넣는 분위기 수(많으면 서로 충돌)
MAX_REFS = 3                              # 음색 참고 파일 수(엔진이 앞·중·뒤 10초씩 뽑음)
COMPARE_SECONDS = 60
MAIN_MODEL = "acestep-v15-turbo"
# 비교 시험(모델 비교): (모델, 파일 이름 꼬리표, 화면 이름, 내려받기 크기 GB(대략), 필요한 그래픽 메모리 GB)
MODELS = [
    (MAIN_MODEL, "기본", "기본 모델", 0, 0),
    ("acestep-v15-turbo-shift3", "shift3", "shift3(선명한 음색)", 5, 0),
    ("acestep-v15-xl-turbo", "XL", "XL(큰 모델)", 10, 20),
]

LEN_MIN, LEN_MAX = 10, 600


def instr_label(keys) -> str:
    return "+".join(INSTR[k][0] for k in keys if k in INSTR) or "피아노"


def clamp_len(sec) -> float:
    return float(min(LEN_MAX, max(LEN_MIN, round(float(sec)))))


STYLE = {
    "piano": ("Solo piano, neo-classical",
              "An expressive solo grand piano piece performed live by a pianist, with natural dynamics, gentle rubato "
              "and sustain pedal. Warm, rich acoustic piano tone, recorded in an intimate room with natural reverb"),
    "strings": ("modern classical, string ensemble",
                "A string ensemble piece with legato bowing and natural swells, performed live. Lush, warm string tone, "
                "recorded in a concert hall"),
    "guitar": ("acoustic guitar, fingerstyle",
               "A solo fingerstyle acoustic guitar piece performed live, with natural dynamics. Warm, woody guitar tone, "
               "close-miked in a quiet room"),
    "harp": ("classical, solo harp",
             "A solo concert harp piece performed live, with flowing arpeggios. Clear, resonant harp tone with natural reverb"),
    "cello": ("classical, solo cello",
              "A solo cello piece performed live with expressive vibrato and legato phrasing. Deep, warm cello tone, "
              "recorded in a small hall"),
    "violin": ("classical, solo violin",
               "A solo violin piece performed live with expressive vibrato and legato phrasing. Sweet, singing violin tone, "
               "recorded in a small hall"),
    "flute": ("classical, solo flute",
              "A solo flute piece performed live with natural breath and phrasing. Airy, pure flute tone with natural reverb"),
    "orchestra": ("orchestral, cinematic",
                  "A full symphony orchestra piece performed live, with strings, woodwinds and brass. Rich, wide orchestral "
                  "sound recorded in a concert hall"),
}
_ALWAYS_BAN = ["vocals", "singing", "drums", "percussion", "electric bass", "synthesizer", "electric guitar", "beat"]
# 원곡 이해(LM 이 쓴 묘사)에서 분위기 단어만 골라 씀 — 원곡 악기 이름이 지시문에 섞이지 않게
MOOD_WORDS = ("melancholic melancholy uplifting energetic dreamy dark nostalgic euphoric intimate warm bright emotional "
              "gentle calm peaceful serene cinematic epic hopeful sad romantic playful dramatic tender bittersweet lively "
              "soothing mysterious heartfelt wistful joyful cheerful somber reflective ethereal triumphant tense "
              "relaxing sentimental passionate delicate majestic").split()


def mood_words(text: str, limit: int = 4) -> list[str]:
    low = _re.findall(r"[a-z]+", (text or "").lower())
    out = []
    for w in low:
        if w in MOOD_WORDS and w not in out:
            out.append(w)
    return out[:limit]


def caption(instr_keys, mood_keys, extra_moods=()) -> str:
    """새 지시문. 악기 1개 = 그 악기 장르·묘사, 여러 개 = 첫 악기 묘사 + 편성 문장. 분위기는 최대 2개(충돌 줄이기)."""
    ks = [k for k in instr_keys if k in INSTR] or ["piano"]
    genre, desc = STYLE[ks[0]]
    parts = [genre + ".", desc + "."]
    if len(ks) > 1:
        names = [INSTR[k][1] for k in ks]
        parts.append("Performed only by " + ", ".join(names[:-1]) + " and " + names[-1] + ".")
    moods = list(extra_moods) or [w.strip() for m in mood_keys[:MAX_MOODS] if m in MOOD for w in MOOD[m][1].split(",")]
    if moods:
        parts.append(", ".join(dict.fromkeys(moods)).capitalize() + " mood.")
    return " ".join(parts)


def negative(instr_keys) -> str:
    """작곡 담당(LM)에게 '피할 것': 늘 빼는 것 + 고르지 않은 악기."""
    ks = set(instr_keys)
    other = [en for k, _, en in INSTRUMENTS if k not in ks and "orchestra" not in ks]
    return ", ".join(_ALWAYS_BAN + other)


def lyrics(instr_keys) -> str:
    """연주곡 전개(구조 태그) — 엔진 튜토리얼 'Writing Instrumental Music' 방식."""
    name = INSTR[(list(instr_keys) or ["piano"])[0]][1] if (list(instr_keys) or ["piano"])[0] in INSTR else "piano"
    return (f"[Intro - soft {name}]\n\n[Main Theme - {name} melody]\n\n[Development - expressive]\n\n"
            f"[Climax - fuller]\n\n[Outro - fade out]")


def request(*, instr_keys, mood_keys, seconds, bpm=None, key_scale="", profile=None, seed=None, ref_path=None,
            candidates=1, model=MAIN_MODEL) -> dict:
    """ACE-Step /release_task JSON(text2music). ref_path = 서버가 읽을 음색 참고 음원(엔진 tmp 안 절대 경로)."""
    req = {
        "prompt": caption(instr_keys, mood_keys),
        "lyrics": lyrics(instr_keys),
        "vocal_language": "unknown",
        "lm_negative_prompt": negative(instr_keys),
        "task_type": "text2music",
        "thinking": bool(profile.thinking and profile.lm_ready) if profile is not None else False,
        "audio_duration": clamp_len(seconds),
        "batch_size": max(1, min(4, int(candidates))),
        "audio_format": "wav",
        "inference_steps": 8,
        "use_cot_caption": False,             # 지시문을 언어 모델이 바꾸지 않게(악기 지정 유지 · 엔진 공식 화면 기본과 같음)
        "use_cot_language": False,
        "time_signature": "4",
        "model": model,                       # 늘 적음 — 서버가 필요하면 그 모델로 바꿔 읽음(ACESTEP_ON_DEMAND_MODEL_LOAD)
    }
    if bpm:
        req["bpm"] = int(round(min(300, max(30, float(bpm)))))
    if key_scale:
        req["key_scale"] = key_scale
    if seed is not None:
        req["use_random_seed"] = False
        req["seed"] = int(seed)
    if ref_path:
        req["reference_audio_path"] = str(ref_path)
    return req


# ------------------------------------------------------------ 결과 파일 이름
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
