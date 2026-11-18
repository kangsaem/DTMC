"""악기·분위기·원곡 반영 정도 → ACE-Step 요청(caption·bpm·key_scale·task_type…).

원곡 반영 정도(기본 = 낮음, 저작권 안전 쪽):
- 낮음: 원곡 소리를 엔진에 보내지 않음. 분석한 템포·조성·분위기만 글로 전달(text2music).
- 중간·높음: 원곡을 목표 길이로 맞춘 참고 음원을 보내 커버(cover) — 강도 0.3 / 0.6.
  강도가 높을수록 원곡 구조·멜로디를 더 따라감(1단계 시험에서 값 조정 예정).
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

STRENGTHS = [("low", "낮음"), ("mid", "중간"), ("high", "높음")]
COVER_STRENGTH = {"low": None, "mid": 0.3, "high": 0.6}

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
    moods = [MOOD[m][1] for m in mood_keys if m in MOOD]
    parts = [lead, "instrumental"] + moods + ["acoustic performance", "high quality recording"]
    return ", ".join(parts)


def clamp_len(sec) -> float:
    return float(min(LEN_MAX, max(LEN_MIN, round(float(sec)))))


def request(*, instr_keys, mood_keys, seconds, bpm=None, key_scale="", strength="low", src_path=None,
            candidates=1, profile=None, seed=None) -> dict:
    """ACE-Step /release_task JSON. src_path = 서버가 읽을 참고 음원(같은 컴퓨터의 절대 경로)."""
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
    cs = COVER_STRENGTH.get(strength)
    if cs is not None and src_path:
        req["task_type"] = "cover"
        req["src_audio_path"] = str(src_path)
        req["audio_cover_strength"] = cs
    else:
        req["task_type"] = "text2music"
        req["thinking"] = bool(profile.thinking and profile.lm_ready) if profile is not None else False
    return req
