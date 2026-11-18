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
MAIN_MODEL = "acestep-v15-turbo"                 # 엔진 설치 때 받는 모델(shift3 가 아직 없을 때만 씀)
GEN_MODEL = "acestep-v15-turbo-shift3"           # 기본 생성 모델(v0.1.9, 사용자 시험: 기본 모델보다 좋음) — 처음 한 번 내려받음
XL_TURBO = "acestep-v15-xl-turbo"                # 음색 풍부 · 다른 악기가 미세하게 새어 나옴(시험 중)
XL_SFT = "acestep-v15-xl-sft"                    # 50단계 · CFG(지시문 엄격도) 있음
XL_VRAM = 20                                     # XL 을 쓰는 데 필요한 그래픽 메모리(GB)
# 모델: 화면 이름 · 내려받기 크기 GB(대략)
MODEL_INFO = {MAIN_MODEL: ("기본 모델", 0), GEN_MODEL: ("shift3 모델", 5), XL_TURBO: ("XL 모델", 10),
              XL_SFT: ("XL SFT 모델", 10)}
LM_4B = "acestep-5Hz-lm-4B"                      # 큰 작곡 담당(멜로디·구성) — 서버를 다시 켜야 바뀜
MODEL_INFO[LM_4B] = ("작곡 담당 4B", 9)
ALT_VAE = "scragvae"                             # 대체 음원 변환부(커뮤니티 VAE, MIT) — 서버가 처음 쓸 때 스스로 받음
ENV_VAE = {"ACESTEP_VAE_CHECKPOINT": ALT_VAE}
ENV_LM4B = {"ACESTEP_LM_MODEL_PATH": LM_4B}
XL_OPT = {"dcw_enabled": False}                  # XL 은 늘 DCW 끔(사용자 시험 11/18: 켜면 다른 악기가 새어 나옴)


QUALITIES = [("std", "기본"), ("hq", "고품질(XL)")]


def preset(quality: str, big_gpu: bool) -> dict:
    """품질 선택 → 모델 · request 에 더할 값 · 서버 환경(v0.1.11, 사용자 시험 11/18로 정함).
    기본 = shift3(DCW 그대로) + 작곡 담당 4B(큰 그래픽 메모리일 때만) + 대체 음원 변환부
    고품질 = XL(DCW 끔) + 작곡 담당 1.7B + 대체 음원 변환부 — XL + 4B 는 24GB 에서 실패해 함께 안 씀. 8단계 유지(16단계 = 먹먹함)."""
    if quality == "hq" and big_gpu:
        return {"quality": "hq", "model": XL_TURBO, "opt": dict(XL_OPT), "env": dict(ENV_VAE)}
    env = dict(ENV_VAE)
    if big_gpu:
        env.update(ENV_LM4B)
    return {"quality": "std", "model": GEN_MODEL, "opt": {}, "env": env}


# ------------------------------------------------------------ 스타일(영상 배경음악용, v0.1.11 샘플 시험)
# (키, 화면 이름, 지시문(장르 + 묘사), 어울리는 템포 범위) — 원곡 템포가 범위 안일 때만 원곡 템포를 씀
STYLES = [
    ("ambient", "수중 앰비언트", "Ambient, underwater atmosphere. Soft evolving synth pads, slow gentle piano notes and "
     "long lush reverb, deep and spacious, floating and weightless. Calm, serene, dreamy mood.", (50, 95)),
    ("cinematic", "시네마틱", "Cinematic, orchestral soundtrack. Emotional piano with warm string ensemble, slowly building "
     "to a wide, uplifting climax. Rich, polished film-score sound. Hopeful, majestic mood.", (60, 120)),
    ("chill", "칠아웃", "Chillout, downtempo electronica. Smooth electric piano chords, soft mellow beat, warm sub bass and "
     "airy pads, laid-back groove. Relaxed, sunny, peaceful mood.", (75, 110)),
    ("tropical", "트로피컬 어쿠스틱", "Tropical acoustic, feel-good. Bright fingerpicked acoustic guitar and ukulele, light hand "
     "percussion and shaker, gentle bass. Warm live recording. Cheerful, breezy, carefree mood.", (90, 130)),
    ("lofi", "로파이", "Lo-fi hip hop, chill. Warm mellow electric piano, relaxed dusty drum beat, soft bass and vinyl "
     "crackle, jazzy chords. Cozy, nostalgic, laid-back mood.", (65, 95)),
    ("piano", "피아노 중심", "Neo-classical, piano with strings. Expressive grand piano melody performed live with natural "
     "dynamics, joined by subtle warm strings. Intimate room recording. Tender, reflective mood.", (55, 110)),
    ("pop", "밝은 팝 연주곡", "Upbeat pop instrumental. Bright piano and clean electric guitar, punchy drums and melodic bass, "
     "catchy hook. Polished modern production. Uplifting, energetic, joyful mood.", (100, 135)),
    ("jazz", "재즈 트리오", "Jazz trio, smooth lounge jazz. Elegant piano improvisation, walking upright bass and soft brushed "
     "drums. Warm intimate club recording. Relaxed, sophisticated mood.", (80, 140)),
    ("epic", "에픽 오케스트라", "Epic orchestral, trailer music. Powerful full orchestra with soaring strings, bold brass and "
     "thunderous percussion, driving ostinato. Huge cinematic sound. Triumphant, heroic mood.", (90, 150)),
    ("synthwave", "신스웨이브", "Synthwave, retro 80s. Analog synth arpeggios, lush pads, steady electronic drums and pulsing "
     "bass. Night drive atmosphere. Dreamy, nostalgic, driving mood.", (90, 125)),
    # 아래 둘 = 엔진 장르 목록의 'J-Rock anime opening' · 'hard rock'/'instrumental rock' + 공식 예제의 표현(보컬 부분은 뺌)
    ("anime", "애니 오프닝", "J-Rock anime opening, instrumental. Fast-paced energetic rock with powerful electric guitars, "
     "driving drums and melodic bass, bright synth lead carrying a catchy heroic melody, dramatic orchestral hits. "
     "Anthemic, exciting, uplifting mood.", (150, 185)),
    ("hardrock", "하드록", "Hard rock, instrumental rock. Heavy distorted electric guitar riffs and a soaring melodic lead "
     "guitar solo, punchy live drums and driving bass, thick wall of rhythm guitars. Raw, powerful live band sound. "
     "Energetic, bold, confident mood.", (110, 160)),
]
STYLE_LYRICS = "[Intro]\n\n[Main Theme]\n\n[Build]\n\n[Outro - fade out]"
STYLE_NEGATIVE = "vocals, singing, voice, rap, choir, spoken word"


STYLE_BY = {st[0]: st for st in STYLES}
DEFAULT_STYLE = "chill"


def style_caption(key: str) -> str:
    return STYLE_BY.get(key, STYLE_BY[DEFAULT_STYLE])[2]


def style_request(style, *, seconds, bpm=None, key_scale="", profile=None, seed=None, ref_path=None,
                  model=GEN_MODEL, caption="", force_bpm=False, **extra) -> dict:
    """스타일 한 곡 요청. style = 키 또는 STYLES 항목 · caption = 직접 고친 지시문(비면 스타일 것).
    템포: force_bpm(직접 정함)이면 그대로, 아니면 원곡 템포가 스타일 범위 안일 때만(밖이면 엔진이 정함)."""
    st = STYLE_BY.get(style, STYLE_BY[DEFAULT_STYLE]) if isinstance(style, str) else style
    lo, hi = st[3]
    use_bpm = bpm if bpm and (force_bpm or lo <= float(bpm) <= hi) else None
    req = request(instr_keys=["piano"], mood_keys=[], seconds=seconds, bpm=use_bpm, key_scale=key_scale,
                  profile=profile, seed=seed, ref_path=ref_path, model=model, **extra)
    req.update({"prompt": (caption or "").strip() or st[2], "lyrics": STYLE_LYRICS, "lm_negative_prompt": STYLE_NEGATIVE})
    return req


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


def caption(instr_keys, mood_keys, extra_moods=(), emphasize=False) -> str:
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
    if emphasize:        # 엔진 튜토리얼: 원하는 요소는 단어를 반복해 강화
        names = [INSTR[k][1] for k in ks]
        who = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        alone = "unaccompanied solo " + names[0] if len(names) == 1 else "no other instruments join"
        parts.append(f"Only {who} from start to end, {alone}, nothing but {who}.")
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
            candidates=1, model=GEN_MODEL, lyrics_plain=False, emphasize=False, **extra) -> dict:
    """ACE-Step /release_task JSON(text2music). ref_path = 서버가 읽을 음색 참고 음원(엔진 tmp 안 절대 경로).
    lyrics_plain = 가사 칸에 [Instrumental] 만 · emphasize = 지시문에서 악기 반복 강조 ·
    extra = 그대로 더할 엔진 값(lm_cfg_scale · lm_temperature · dcw_enabled · inference_steps · guidance_scale)."""
    req = {
        "prompt": caption(instr_keys, mood_keys, emphasize=emphasize),
        "lyrics": "[Instrumental]" if lyrics_plain else lyrics(instr_keys),
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
    req.update(extra)
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


def stem(title: str, source_name: str, style_ko: str, tag, seconds) -> str:
    """결과 파일 이름: [제목]_스타일_번호_길이. 제목이 비면 원곡 이름 첫 단어, 원곡도 없으면 스타일만."""
    t = safe(title.strip()) if title and title.strip() else (first_word(source_name) if source_name else "")
    return "_".join(x for x in (t, safe(style_ko), str(tag), len_tag(seconds)) if x)


def style_stem(title: str, source_name: str, n: int, style_ko: str, seconds) -> str:
    """스타일 샘플 파일 이름: [제목]_번호-스타일_길이."""
    t = safe(title.strip()) if title and title.strip() else (first_word(source_name) if source_name else "샘플")
    return f"{t}_{n:02d}-{safe(style_ko)}_{len_tag(seconds)}"
