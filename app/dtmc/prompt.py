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
COMPARE_STRENGTHS = (30, 50, 70, 90)        # v0.1.6 커버 비교(지금은 안 씀)
COMPARE_SECONDS = 60                      # v0.1.7 상한 확인 시험
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


# ------------------------------------------------------------ v0.1.7 새 지시문(엔진 문서 방식) — 상한 확인 시험용
# 장르 + 묘사 문장(연주·음색·녹음) + 분위기 1~2개. 금지어는 지시문이 아니라 작곡 담당(LM) 전용 칸(lm_negative_prompt).
# 장르 단어는 엔진 장르 목록(acestep/genres_vocab.txt)에 있는 것.
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


def caption2(instr_keys, mood_keys, extra_moods=()) -> str:
    """새 지시문. 악기 1개 = 그 악기 장르·묘사, 여러 개 = 첫 악기 묘사 + 편성 문장. 분위기는 최대 2개(충돌 줄이기)."""
    ks = [k for k in instr_keys if k in INSTR] or ["piano"]
    genre, desc = STYLE[ks[0]]
    parts = [genre + ".", desc + "."]
    if len(ks) > 1:
        names = [INSTR[k][1] for k in ks]
        parts.append("Performed only by " + ", ".join(names[:-1]) + " and " + names[-1] + ".")
    moods = list(extra_moods) or [w.strip() for m in mood_keys[:2] if m in MOOD for w in MOOD[m][1].split(",")]
    if moods:
        parts.append(", ".join(dict.fromkeys(moods)).capitalize() + " mood.")
    return " ".join(parts)


def negative2(instr_keys) -> str:
    """작곡 담당(LM)에게 '피할 것': 늘 빼는 것 + 고르지 않은 악기."""
    ks = set(instr_keys)
    other = [en for k, _, en in INSTRUMENTS if k not in ks and "orchestra" not in ks]
    return ", ".join(_ALWAYS_BAN + other)


def lyrics2(instr_keys) -> str:
    """연주곡 전개(구조 태그) — 엔진 튜토리얼 'Writing Instrumental Music' 방식."""
    name = INSTR[(list(instr_keys) or ["piano"])[0]][1] if (list(instr_keys) or ["piano"])[0] in INSTR else "piano"
    return (f"[Intro - soft {name}]\n\n[Main Theme - {name} melody]\n\n[Development - expressive]\n\n"
            f"[Climax - fuller]\n\n[Outro - fade out]")


def request2(*, instr_keys, mood_keys, seconds, bpm=None, key_scale="", profile=None, seed=None, ref_path=None,
             extra_moods=(), candidates=1) -> dict:
    """새 방식 요청(text2music): 새 지시문 + 구조 태그 + 금지어 칸 + (선택) 음색 참고 음원."""
    req = request(instr_keys=instr_keys, mood_keys=mood_keys, seconds=seconds, bpm=bpm, key_scale=key_scale,
                  strength=0, candidates=candidates, profile=profile, seed=seed)
    req["prompt"] = caption2(instr_keys, mood_keys, extra_moods)
    req["lyrics"] = lyrics2(instr_keys)
    req["vocal_language"] = "unknown"
    req["lm_negative_prompt"] = negative2(instr_keys)
    if ref_path:
        req["reference_audio_path"] = str(ref_path)
    return req


def understand_request(src_path) -> dict:
    """원곡 이해: 원곡 → 설계도 코드 → 작곡 담당이 묘사·장르·템포·조성을 글로(소리는 생성에 안 씀)."""
    return {"full_analysis_only": True, "src_audio_path": str(src_path), "task_type": "text2music",
            "prompt": "", "lyrics": ""}
