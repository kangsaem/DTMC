# HANDOFF — DTMC (Desktop Music Composer)

> 새 대화에서 이어서 작업할 때 이 파일을 기준으로 한다.
> 마지막 갱신: 2026-11-18 · **현재 = v0.1.11(품질 선택 기본/고품질 · 대체 음원 변환부 기본 · 비교 시험 = 스타일 샘플 10곡) → 사용자 4090 시험 대기**

## 한 줄 요약
원곡(일반 음원)을 넣으면 분위기가 비슷한 새 곡을 **피아노 또는 스트링** 연주로, **지정한 길이**만큼 만들어 주는 **윈도우 전용 로컬 프로그램**.

- 계획서(상세): https://claude.ai/artifact/CMAdjYyGgieR8usnh3DWgX
- 형제 프로그램: [DTME](https://github.com/kangsaem/DTME) · [MOSCO](https://github.com/kangsaem/MOSCO) — **화면·버튼·빌드 틀을 이 둘과 통일**

## 확정 사항
| 항목 | 결정 |
| --- | --- |
| 운영체제 | 윈도우 10/11 전용 |
| 대상 기기 | RTX 4090 데스크톱, 노트북 ThinkBook 15 G5 ABP (AMD Ryzen 5 7530U, RAM 총 16GB, NVIDIA 없음) |
| 실행 위치 | 각 사용자 컴퓨터 안(로컬). 인터넷은 첫 실행 때 엔진·모델 다운로드에만 |
| 화면 | PySide6 윈도우 프로그램, DTME·MOSCO와 같은 틀 (HTML·웹 화면 안 씀) |
| 원격·모바일 | 제외 |
| 입력 | 일반 음원 (MP3, WAV, FLAC, M4A) |
| 악기 | 목록에서 최대 3개 조합: 피아노, 스트링, 어쿠스틱 기타, 하프, 첼로, 바이올린, 플루트, 오케스트라 (1단계 품질 시험 후 확정) |
| 길이 | 10초 ~ 10분 (생성 전 예상 시간 표시) |
| 후보 개수 | 같은 설정으로 만들 곡 수. 기본 2곡(노트북 1곡), 최대 4곡 |
| 출력 형식 | MP3, FLAC, M4A, OGG, WAV |
| 배포 | 포터블 패키지 **통합 1종**(4090·노트북 같은 파일). 엔진은 첫 실행 때 설치 |
| 용도 | 개인용으로 시작, 상업용 확장 고려 |
| 저장소 | GitHub `kangsaem/DTMC` (비공개) |

## 기술 구성
| 역할 | 선택 |
| --- | --- |
| 생성 엔진 | ACE-Step 1.5 (MIT, 생성물 상업 사용 가능, CUDA·CPU 지원) |
| 화면 | Python 3.12 + PySide6 6.12 (Fusion + 어두운 팔레트, 글꼴 지정 없음) |
| 버튼 모양 | DTME `app/dtme/ui_style.py` 그대로 복사 (기준서 DTME `design/BUTTON_UI.md`) |
| 공용 부품 | MOSCO `app/mosco/single.py`(하나만 실행) · `taskbar.py`(작업 표시줄 진행) 복사 |
| 원곡 분석 | numpy 자체 구현(`analyze.py`, 추가 라이브러리 없음): 템포(onset 자기상관)·조성(크로마 + Krumhansl)·크기·밝기 → 분위기 |
| 형식 변환 | ffmpeg (BtbN 빌드, DTME·MOSCO와 같게) |
| 빌드 | GitHub Actions(windows-latest) + PyInstaller onedir, 결과 `DTMC-<버전>-windows` (DTME·MOSCO `build.yml` 참고) |
| 엔진 설치 | 첫 [AUTO] 때 설치 창: engine_src(ACE-Step 고정 커밋 `ca1e85f`)를 엔진 폴더로 복사 → `uv sync --frozen --python 3.11` → `python -m acestep.model_downloader` → `uv cache clean` → installed.json |
| 엔진 실행 | 엔진 폴더 Python 으로 ACE-Step REST API 서버(127.0.0.1 빈 포트) → `/release_task` · `/query_result` · `/v1/audio`. 윈도우 Job Object 로 DTMC 와 함께 종료 |

- GPU판 PyTorch는 NVIDIA가 없으면 CPU로 동작 → 패키지 1종으로 두 기기 지원
- 버전 = `app/dtmc/__init__.py` 의 `__version__`

흐름: DTMC 창 → 작업 관리(단계 진행·취소) → 원곡 분석(지시문) → ACE-Step 생성(WAV) → ffmpeg 변환 → DTMC 창(미리듣기·폴더 열기)

## 화면 (DTME·MOSCO 틀)
1. 시작 창: 제목 표시줄 없음, 400×400 그림(`assets/start.png`) + 왼쪽 위 'DTMC 버전', [AUTO]
2. 작업 창 왼쪽: 원곡 목록(끌어다 놓기·우클릭 추가·Del 제거)
3. 작업 창 오른쪽: 단계 줄 3개(체크 = 켬/끔, 줄 클릭 = 펼침)
   - 분석: 템포·조성·길이·분위기 확인·수정
   - 생성: 악기(최대 3개 체크), 길이(10초~10분), 원곡 반영 정도(기본 낮음), 템포, 후보 개수(기본 2)
   - 출력: 형식, 저장 폴더
4. [AUTO]: 단계별 진행 막대 · [취소] · 작업 표시줄 진행 · 창 제목 진행률
5. 완료: 결과 미리듣기 · [폴더 열기][닫기]
6. 첫 실행: 엔진·모델 다운로드 창(한 번만, 디스크 공간 확인·이어받기)
- 실행 모드(GPU/CPU) 항상 표시 · 기준 해상도 1920×1080

## 방향(11/18 15:15 확정) — 커버가 아니라 '새 곡'
- 목표 3가지: ① 원곡과 **분위기**가 비슷 ② **원곡과 달라도 그 자체로 좋은 멜로디** ③ **고른 악기만, 자연스러운 실제 악기 소리**.
- v0.1.5~0.1.6 의 커버(원곡 반영 %)는 목표와 구조적으로 안 맞음(Claude 가 잘못 추천): 커버 = 원곡을 5Hz 설계도 코드로 바꿔 작곡 담당(LM) 자리에 넣음 → 멜로디·리듬·화음·**편성**·일부 음색이 따라옴, LM 은 꺼짐. 실사용: 90% = 거의 원곡 · 50% = 음악 같지 않음 · 원곡 악기 그대로 · 0% = 미디 같은 피아노.
- 엔진 구조(코드·문서 `ca1e85f` 확인): 지시문 → [글 해석기] / [작곡 담당 LM 5Hz: 설계도 = 멜로디·편성] → [연주 담당 DiT: 음색·믹싱] → VAE.
- 목표별 엔진 수단: ① 원곡 이해(`full_analysis_only`: 원곡 → 코드 → LM 이 묘사·장르·bpm·조성을 글로, 소리는 생성에 안 씀) + numpy 분석 ② text2music + thinking(LM 1.7B, 4090 은 4B 가능) + 가사 칸 구조 태그 + 여러 곡 뽑아 고르기 ③ `reference_audio_path` = **잘 녹음된 실제 악기 연주**(10초 × 3 조각 → 음색·연주·녹음 질감만) + 장르 단어(엔진 장르 목록: Solo piano · neo-classical 등) + `lm_negative_prompt`(LM 전용 금지어 — 지시문 안 "no drums" 는 잘못) + 모델(turbo-shift3 · XL turbo · XL SFT).
- 그 밖 확인: turbo 는 CFG 없음(guidance 1.0 고정) · `use_cot_caption` 은 공식 화면 기본 끔(DTMC 와 같음) · 가사 칸이 정확히 `[Instrumental]` 일 때만 서버가 연주곡으로 표시(생성 자체엔 안 쓰임) · text2music 에선 cover 강도 값 무시 · 참고 음원 경로도 서버 임시 폴더 안만 · XL 은 CPU(float32 약 16GB)라 노트북 불가 · 자동 점수는 Gradio 전용.
- ③이 엔진만으로 안 되면: 결과 → 음표(MIDI) 받아 적기 → 실제 피아노 샘플로 다시 연주(엔진 밖, 미착수).

## 변경 이력
- v0.1.11: **v0.1.10 실기 결과**: 대체 음원 변환부(05·06) = 더 실제 연주처럼 · 16단계(03·04) = 먹먹함(제외, 8단계 유지) · 08(XL + 작곡 4B) = 실패(24GB 부족 추정) · 사용자 선택 = "shift3 + 4B + 대체 변환부 / XL + 대체 변환부". → ① **품질 선택**(`prompt.preset`, 생성 단계 '품질' — XL 쓸 수 있는 컴퓨터(그래픽 메모리 20GB 이상)에서만 보임): **기본 = shift3(DCW 켬) + 작곡 담당 4B + 대체 변환부 · 고품질 = XL(DCW 끔) + 작곡 담당 1.7B + 대체 변환부**. 그 밖 컴퓨터 = shift3 + 그 기기 작곡 담당 + 대체 변환부. [AUTO]·악기 시험·비교 시험 모두 적용. 품질을 바꾸면 서버 환경(작곡 담당)이 달라 엔진을 다시 켬(`MakeJob._engine` → `Engine.start(extra_env)`). ② **대체 음원 변환부(scragvae) 기본** — 그 환경으로 엔진이 안 켜지면(못 받음 등) 공식 변환부로 한 번 더 켬. 작곡 4B 를 못 받으면 평소 것으로. ③ **비교 시험 = 스타일 샘플**(`prompt.STYLES` 10가지: 수중 앰비언트 · 시네마틱 · 칠아웃 · 트로피컬 어쿠스틱 · 로파이 · 피아노 중심 · 밝은 팝 연주곡 · 재즈 트리오 · 에픽 오케스트라 · 신스웨이브), 60초 · 같은 씨앗 · 고른 품질. 원곡에서는 조성과(스타일 템포 범위 안일 때만) 템포. 금지어 = 보컬류만. 파일 `제목_01-수중 앰비언트_1m.mp3`. ④ 생성 단계 안내 글 2줄을 말풍선으로(품질 줄이 들어가도 스크롤 없이).
  - 배경: 사용자 "꼭 피아노 솔로가 아니어도 됨 · 주로 다이빙 영상 BGM, 다른 종류도 됨"(11/18 17:33~34).
  - **여기서 못 본 것: shift3 + 4B + 대체 변환부를 합친 소리(각각만 들어 봄) · 품질 바꿀 때 엔진 다시 켜기.**
- v0.1.10: **비교 시험 교체**(사용자 11/18 17:22). 정해진 것: **shift3 = DCW 그대로 켬**(끄는 시험 안 함) · **XL = 늘 DCW 끔**(`prompt.XL_OPT`; DCW 는 '현장감·라이브 느낌'을 주기도 한다는 사용자 의견 — 켜면 XL 에서 다른 악기가 새어 나옴). 이 두 기준에서 하나씩만 바꿈(60초 · 같은 씨앗 · 같은 지시문): 01 shift3 지금 · 02 XL 지금 · 03 shift3 16단계 · 04 XL 16단계 · 05 shift3 대체 음원 변환부 · 06 XL 대체 음원 변환부 · 07 shift3 작곡 담당 4B · 08 XL 작곡 담당 4B.
  - 대체 음원 변환부 = 엔진에 등록된 커뮤니티 VAE `scragvae`(MIT) — 서버 환경 `ACESTEP_VAE_CHECKPOINT=scragvae`, 서버가 처음 쓸 때 스스로 받음. 작곡 담당 4B = `ACESTEP_LM_MODEL_PATH=acestep-5Hz-lm-4B`(DTMC 가 먼저 내려받음, 약 9GB 어림). 둘 다 **서버를 다시 켜야** 바뀜 → `Engine.start(extra_env=…)`: 환경이 지금 서버와 다르면 끄고 다시 켬(다음 [AUTO] 는 평소 환경으로 스스로 되돌아감). 서버는 그래픽 메모리 등급에 안 맞으면 작곡 담당을 말없이 다른 것으로 바꾸므로 로그("LLM model loaded:")로 확인.
  - 한 곡이 실패해도(메모리 부족 등 — XL + 작곡 4B 는 24GB 에 빠듯) 그 곡만 건너뛰고 계속, 끝 글에 'n곡 실패'.
  - 뺀 것(효과 없었음): 가사 칸 [Instrumental]만 · 악기 반복 강조 · 작곡 보수적 · XL SFT. `request` 의 해당 옵션(lyrics_plain · emphasize)은 남아 있음.
  - **여기서 못 본 것: 서버 다시 켜기로 변환부·작곡 담당 바꾸기(가짜 엔진으로만 시험).**
- v0.1.9: **v0.1.8 실기 결과**(4090): 모델 내려받기·서버 모델 전환 동작함 · 기본 < **shift3(좋음)** · XL = 음색 풍부하나 **고르지 않은 악기가 미세하게 새어 나옴**. → ① **기본 생성 모델 = turbo-shift3**(`prompt.GEN_MODEL`; 없으면 [AUTO]·[악기 시험] 전에 확인 창 뒤 내려받음 약 5GB; 받아 뒀으면 서버가 켜질 때부터 shift3 를 읽음 `engine.start_model`). ② **비교 시험 = 설정 비교**(`prompt.compare_cases`, 60초 · 같은 씨앗): 01 shift3 기준 / XL turbo: 02 지금 · 03 가사 칸 `[Instrumental]`만(구조 태그의 "Climax - fuller" 가 악기를 부를 수 있음) · 04 지시문 악기 반복 강조 · 05 작곡 담당 보수적(lm_cfg_scale 3.5 · lm_temperature 0.7) · 06 셋 다 · 07 지금 + DCW 끔 · 08 셋 다 + DCW 끔 / XL SFT(50단계 · CFG): 09 엄격도 4 · 10 엄격도 7. XL 못 쓰는 컴퓨터 = 같은 설정을 shift3 로(5~7곡). 설정이 바뀌면 같은 씨앗이어도 멜로디가 달라질 수 있음(가사 칸·작곡 담당 값이 설계도를 바꿈). ③ **엔진 소스 고침(첫 사례)**: `engine.patch_dcw` — 엔진 시작 전 `acestep/api/http/release_task_request_builder.py` 에 3줄(dcw_enabled · dcw_scaler · dcw_high_scaler 읽기) 추가. 이유: 요청 모델엔 dcw_* 칸이 있는데 HTTP 경로가 안 읽어 끌 수 없음. 자리를 못 찾으면 DCW 곡(07·08)만 빠짐. ④ 진행 막대는 그대로(사용자: 바꾸지 말 것).
  - 엔진 확인: XL turbo 공식 기본 = 8단계 · shift 3.0 · CFG 없음 · DCW 켬(DTMC 와 같음). DCW 권장 세기는 Think 켬 0.02/0.06 · 끔 0.05/0.02 인데 API 는 늘 0.05/0.02. turbo 계열엔 연주 담당 쪽 '금지' 수단이 없음(lm_negative_prompt 는 작곡 담당만).
  - **여기서 못 본 것: XL SFT 내려받기·전환, 고친 엔진으로 DCW 끄기(가짜 엔진으로만 시험).**
- v0.1.8: **[AUTO] 를 새 방식으로 교체, 커버 삭제.** ① 생성 = text2music 만: 지시문(장르 + 묘사 문장 + 분위기 2개까지) · 가사 칸 구조 태그 · vocal_language unknown · lm_negative_prompt · 요청마다 `model` 적음(`prompt.request`). 원곡에서는 템포·조성·분위기만(원곡 소리 안 보냄). ② **원곡 반영 슬라이더·커버 코드 삭제**(옛 설정의 strength 는 무시). [원곡] 버튼 = 처음부터 재생. ③ 템포 [원곡 템포|직접] 잠금 없음. ④ 분위기 최대 2개. ⑤ 음색 참고 최대 3개(`ff.make_ref`: 90초를 n등분해 이어 붙임 → 엔진이 앞·중·뒤 10초씩), [AUTO]·악기 시험 아닌 생성·비교 시험에 적용, 비우면 안 씀. ⑥ **비교 시험 = 모델 비교**: 60초 · 같은 지시문·씨앗 · 기본 / turbo-shift3 / XL turbo(그래픽 메모리 20GB 이상만). 없는 모델은 확인 창(크기·남은 공간) 뒤 `python -m acestep.model_downloader --model X --dir … --skip-main` 으로 받고 `checkpoints/X/.dtmc_ok` 표시. 모델 전환 = 서버 환경 `ACESTEP_ON_DEMAND_MODEL_LOAD=true`(+ 일꾼 1개) → 요청의 model 이 다르면 서버가 바꿔 읽음. 서버는 실패하면 말없이 기본 모델로 만들므로 로그 줄("Model loaded on demand:")로 확인해 다르면 오류(`Engine.cur_model`). ⑦ 파일 = `제목_악기_번호_길이` 유지 · 비교 = `제목_악기_기본|shift3|XL_1m`. ⑧ 작업 기록에 지시문·가사 칸·금지어·모델·씨앗·음색 참고.
  - **여기서 못 본 것: 실제 모델 내려받기·서버의 모델 전환(가짜 엔진·가짜 내려받기로만 시험).** 모델 크기(shift3 약 5GB · XL 약 10GB)는 어림값.
  - 남겨 둔 것(안 씀): `prompt.mood_words` · `Engine.understand`(원곡 이해, 보류).
- v0.1.7: **[비교 시험]을 '엔진 상한 확인'으로 교체**(커버 비교 삭제). 첫 원곡 기준 60초 · 같은 씨앗 · 원곡 소리는 생성에 안 씀:
  1 지금 방식(v0.1.6 0%) · 2 새 지시문(`prompt.request2`: 장르 + 묘사 문장 + 분위기 2개까지 · 가사 칸 구조 태그 · vocal_language unknown · lm_negative_prompt) · 3 + 음색 참고(생성 단계 '음색 참고' 칸에 파일을 넣었을 때만) · 4 + 원곡 이해(언어 모델 있을 때만; LM 묘사에서 **분위기 단어만** 골라 지시문에 — 원곡 악기 이름이 안 섞이게, `prompt.mood_words`).
  파일 `제목_악기_시험N_이름_1m.ext`. 작업 기록(kind=compare2)에 지시문·금지어·원곡 이해 결과 전부. `Engine.understand` 추가, 생성 요청 로그에 가사 칸 포함. **[AUTO] 는 v0.1.6 그대로(기본 반영 70% 커버) — 시험 결과 보고 다시 짬.**
- v0.1.6: **원곡 반영 강화**(v0.1.5 실사용: '높음'인데 원곡 느낌 없음·다른 악기 섞임·불협화음 → 11/18 14:13 확정안 구현).
  ① 원곡 반영 = 슬라이더 0~100%(5% 단위, 기본 70%, 80% 초과 노란 경고). 0% = text2music, >0 = cover.
  ② **A안**: 0~60% = audio_cover_strength(%/100)만 · 60% 초과부터 cover_noise_strength 0 → 0.6(100%) 직선(70% = 0.15, 90% = 0.45, 잠정). `prompt.cover_params`.
  ③ 반영 > 0 이면 bpm·key 안 보냄(원곡 그대로) · 분석 단계 템포 [원곡 템포|직접]·BPM 칸 비활성(켜진 버튼도 흐린 회청색 — `Seg._grey`, ui_style 은 그대로).
  ④ 악기 지시문: "… , piano only, …, no other instruments, no drums, no percussion, no bass, no synth, no vocals"(DiT 에 부정 지시문 칸이 없어 글 안에).
  ⑤ [비교 시험]: 첫 원곡 앞 30초 × 30·50·70·90%, 같은 씨앗(무작위 1개, 결과 줄·작업 기록에 표시). 파일 `제목_악기_반영70_30s.mp3`.
  ⑥ 결과 줄 [원곡] 버튼: 결과 듣던 위치의 원곡(결과가 원곡보다 길면 원곡 길이로 접음 = 참고 음원이 반복이라), 반영 0% 결과는 처음부터. 원곡 → ▶ 로 돌아가면 같은 회차 위치. ■ 로 멈추면 처음으로.
  ⑦ 파일 이름 = `[제목]_[악기]_[번호]_[길이].ext`(번호는 후보 1곡이어도 붙음). 출력 단계 '제목' 칸(기억 안 함), 비우면 원곡 이름 첫 단어(트랙 번호 같은 숫자 조각 건너뜀). 길이 `30s`·`1m30s`·`2m`. 같은 이름이면 ` (2)`.
  ⑧ 옛 설정 low/mid/high → 0/30/60. 작업 기록에 strength %·cover_strength·noise_strength·seed.
  ⑨ 결과 줄 긴 글은 줄임(…), 아래 줄 = `반영 N% · 원곡 · 악기 · 길이(· 씨앗)`. tests/proto_v016.py 삭제(반영 완료).
  - 확인(ACE-Step `ca1e85f`): API 이름 cover_noise_strength · seed + use_random_seed=false. turbo 모델도 noise 강도를 **가장 가까운 시간 단계로 맞춤**(8단계) → 5% 차이가 실제로는 같은 결과일 수 있음. 비교 시험으로 확인할 것.
- v0.1.5: **실기 버그 — 엔진을 옮긴 뒤 'uv trampoline failed to spawn Python child process'**. 원인 2개: ① 윈도우 uv 가상 환경 python.exe(트램펄린)는 원래 Python 위치를 exe 안에 적어 둠 ② uv 는 python\cpython-3.11-…(부 버전 이름)을 실제 폴더 cpython-3.11.18-… 로 잇는 정션을 만들고 가상 환경 home 이 그 정션을 가리킴(정션은 옛 절대 경로). 수정: home 을 실제 폴더로 · 끊긴 링크 지움 · python.exe 를 표준 venv 실행기(Lib\venv\scripts\nt\venvlauncher.exe, pyvenv.cfg 를 읽음)로 교체 · 그래도 안 되면 원래 Python + site-packages 직접 연결(boot). 엔진 시작 때 점검(ensure_python) → 이미 옮겨진 엔진도 스스로 고침(heal). 시험: 엔진 폴더 안 uv 관리 Python 3.11 가상 환경을 옮겨 실행 · 고장 상태 재현 후 회복.
  실기 로그에서 확인: 모델 로드 정상(DiT turbo 수 초, flash_attention_2) · LM 1.7B(nano-vllm) 준비에 수 분 걸릴 수 있음 · crash.log 의 0x8001010d 는 윈도우가 처리한 예외 기록(죽은 것 아님).
- v0.1.4: **종료 정리**: DTMC 자신을 Job Object(KILL_ON_JOB_CLOSE·BREAKAWAY_OK)에 넣음 → 엔진 Python 이 띄운 손자 Python 까지 DTMC 와 함께 꺼짐(`procs.py`). 시작·종료 때 엔진 폴더·옆 DTMC 버전 폴더의 engine·bin\uv.exe 로 도는 남은 프로세스 정리(ctypes EnumProcesses). 탐색기(폴더 열기)는 Job 밖으로. **엔진 옮기기**: 엔진이 DTMC 버전 폴더 안(…\DTMC_x\engine)이면 시작 때 같은 드라이브 \DTMC_engine 으로 이름 바꾸기 + 가상 환경 경로(pyvenv.cfg · *.pth) 고침 → 옛 버전 폴더 삭제 가능(진짜 uv 가상 환경으로 시험). 옆 폴더는 DTMC.exe 있는 것만 봄. **버튼(A안)**: 오른쪽 위 [−][✕](✕ = 종료, 생성 중이면 확인) · 아래 = 설정 [AUTO] / 생성 중 [취소] / 끝 [폴더 열기][돌아가기] · 시작 창으로 돌아가는 길 없앰. **엔진 준비 경과 시간** 표시. **아이콘 = 시작 그림과 같은 사진**(dtmc.ico 16~256px, tools/make_icon.py).
- v0.1.3: **실기 버그 — '엔진 준비 0%'에서 멈춤**: ACE-Step 서버 기본이 모델을 첫 요청 때 읽음(ACESTEP_NO_INIT 기본 true) → /health models_initialized 가 계속 False. 서버 환경 ACESTEP_NO_INIT=false(시작할 때 읽기). 진행 글에서 서버 접속 기록(GET /health …) 숨김. **엔진 위치 고정**(메모 반영): 새 설치 = C:\DTMC_engine(C: 부족하면 프로그램 드라이브\DTMC_engine) · 설치 위치를 settings(engine_dir, 내 문서\DTMC)에 기억 · 기억이 없으면 고정 위치·이 폴더\engine·옆 버전 폴더\engine 에서 이미 설치된 엔진을 찾아 이어 씀(v0.1.2 이하 설치분 포함). tests/test_engine_env.py 추가.
- v0.1.2: 시작 그림 = 사용자 제공(미쿠 코스프레 + 베토벤, 1254→800px) + 제목 뒤 어두운 그라데이션. 아이콘은 임시 그대로. **주의: 하츠네 미쿠 = 크립톤 캐릭터 → 상업 배포 전 그림 교체 또는 허락 필요(사용자 확인함, 11/18).**
- v0.1.1: ACE-Step 서버는 참고 음원 절대 경로를 '시스템 임시 폴더' 안만 받음(release_task_audio_paths.py) → 서버 TMP/TEMP/TMPDIR = 엔진 tmp(원곡 반영 중간·높음이 400 오류 나던 것). /health 의 models_initialized 까지 기다림. 언어 모델은 설치된 것만 켬. 가짜 엔진도 같은 경로 규칙.
- v0.1.0: 첫 코드(빌드 44220387072 성공 · 시험 2개 · exe 자체 점검 통과 · 아티팩트 218MB)

## 코드 구조 (v0.1.x)
```
app/main.py            진입점(하나만 실행 · 어두운 팔레트 · 시작 창↔작업 창 · DTMC_SELFTEST)
app/dtmc/paths.py      프로그램·엔진·사용자 폴더(내 문서\DTMC) · bin 도구(ffmpeg·ffprobe·uv)
app/dtmc/hw.py         nvidia-smi·RAM·CPU → 실행 모드(GPU 고품질 / CPU 경량), 드라이버 570 미만 경고
app/dtmc/ff.py         ffmpeg: 분석용 PCM · 참고 음원(목표 길이로 반복·자르기) · 형식 변환 · 시험 음원
app/dtmc/analyze.py    원곡 분석(numpy)
app/dtmc/prompt.py     악기 8 · 분위기 7 · 악기별 장르·묘사(STYLE) → 지시문·가사 칸·금지어 → API 요청(text2music) · 비교 모델 목록 · 파일 이름
app/dtmc/engine.py     엔진 설치·서버·요청(ACE_COMMIT 고정 — build.yml 과 같게)
app/dtmc/jobs.py       분석 스레드 · 생성 스레드(엔진 준비 → 분석 → 생성·저장) · 악기 시험 · 비교 시험 · 작업 기록 · 속도 기억(예상 시간)
app/dtmc/ui_start.py   시작 창(DTME Launcher 틀)
app/dtmc/ui_main.py    작업 창(DTME 자동 창 틀: 원곡 목록 + 단계 줄 분석·생성·출력 + 결과 줄 미리듣기·[원곡] 번갈아 듣기)
app/dtmc/ui_engine.py  엔진 설치 창
app/dtmc/ui_widgets.py Seg · Multi · StageRow · StepBar · Grip 등(DTME 모양)
app/dtmc/ui_style.py · single.py · taskbar.py   DTME·MOSCO 에서 그대로 복사(손대지 말 것)
tests/mock_engine.py   가짜 ACE-Step API(사인파) — DTMC_ENGINE_URL 로 연결
tests/test_pipeline.py 반영 %·A안·파일 이름 · 분석 → 생성 → 5형식 저장 · 비교 시험(씨앗·이름·(2)) · 악기 시험 · 취소
tests/test_ui_smoke.py 화면 그리기(ui_shots/) + [AUTO] 실제 실행
tools/make_placeholder.py  임시 시작 그림·아이콘(사용자 그림 받으면 assets/start.png 800×800 · dtmc.ico 교체)
```
- 환경 변수: `DTMC_ENGINE_URL`(가짜 엔진) · `DTMC_ENGINE_DIR` · `DTMC_USER_DIR` · `DTMC_FORCE_CPU=1` · `DTMC_SELFTEST=1` · `DTMC_JOB=0`
- 빌드: push → GitHub Actions(windows) → 아티팩트 `DTMC-<버전>-windows`(bin: ffmpeg·ffprobe·uv, engine_src, assets, LICENSES)
- 여기(Claude 작업 환경)서 확인한 것: 분석(합성음 템포·조성 맞음), 가짜 엔진으로 전체 흐름·5형식·취소, 화면 캡처, 엔진 설치가 uv sync 단계까지 진행(이후 PyTorch 다운로드는 이 환경 네트워크 차단으로 못 함)
- **못 본 것: 실제 ACE-Step 생성(GPU·윈도우·모델 다운로드), 실제 음질, 노트북 CPU 속도**

## 사용자 시험 순서(v0.1.0)
1. Actions 아티팩트 받아 압축 풀기(영문 경로 권장, 예: `D:\DTMC`)
2. DTMC.exe → [AUTO] → 생성 단계 [악기 시험] → 엔진 설치 창 → [설치 시작](수십 분)
3. 설치 끝나면 악기 8개 × 20초 → 음질 판단(1단계 관문)
4. 원곡 넣고 [AUTO] → 결과 확인
5. 문제 생기면 `내 문서\DTMC\logs` 의 dtmc.log · engine.log 공유

## 하드웨어별 동작
- 시작 시 NVIDIA GPU 자동 감지
- RTX 4090: GPU 고품질 모드, 후보 기본 2곡
- 노트북(Ryzen 5 7530U, RAM 16GB): CPU 경량 모드(turbo 모델, 작은 언어 모델 또는 끔), 후보 기본 1곡, 긴 곡은 예상 시간 경고 — 속도·메모리는 3단계에서 측정
  - ACE-Step 권장 RAM 16GB를 딱 충족. 커버(원곡 참고) 작업은 언어 모델을 자동으로 건너뛰어 메모리를 덜 씀
  - AMD 내장 그래픽 가속은 불확실 → 1차는 CPU만

## 개발 단계
| 단계 | 내용 | 완료 기준 | 상태 |
| --- | --- | --- | --- |
| 0. 준비 | 저장소, HANDOFF.md | 저장소에 첫 파일 올라감 | 완료 |
| 1. 엔진 검증 | 프로그램 안 [악기 시험]으로 대신(C안) | 사용자가 음질 합격 판정 | 사용자 시험 대기 |
| 2. 핵심 프로그램 | PySide6 화면, 분석, 형식 변환, 엔진 첫 실행 설치 | 원곡 → 결과 파일 완주 | v0.1.0 작성 완료, 실기 대기 |
| 3. 노트북 대응 | 경량 모드, 속도 측정 | 길이 상한 확정 | 대기 |
| 4. 배포 | GitHub Actions 포터블 빌드, 오류 안내 | 새 PC에서 압축 풀고 실행 성공 | 빌드 설정 완료 |
| 5. 이후 | 유사도 검사, 상업 배포 | — | 대기 |

1단계 음질 확인이 진행 여부를 가르는 관문이다.

## 저작권·라이선스 원칙
- 원곡 반영 정도: v0.1.6 부터 기본 70%(사용자 결정 11/18) · 80% 초과 노란 경고 · 0% = 원곡 소리 안 보냄 · "권리가 있는 음원만 넣으세요" 안내 · 생성 조건 작업 기록
- ffmpeg(GPL 빌드)·PySide6(LGPL) 고지 동봉
- 상업 판매 전 전문가 검토

## 결정 필요 사항
- [x] 시작 창 그림 — 사용자 제공(v0.1.2), 상업 배포 전 교체 검토
- [x] 아이콘 — 시작 그림과 같은 사진(v0.1.4)
- [ ] 악기 목록 최종 확정 — 1단계 품질 시험 후

## 작업 방식 메모
- Claude 작업 환경에는 윈도우·GPU 없음 → 코드 작성·화면 캡처(offscreen)는 Claude, 실행 테스트는 사용자 PC
- 오류는 로그 파일로 남겨 공유(MOSCO처럼 `내 문서\DTMC\logs\` 제안)
- 빌드 올릴 때마다 Actions 링크 첨부(DTME·MOSCO 규칙과 같게)
- 이 세션에서 push 하려면 GitHub → Settings → Applications → Claude → Configure 에서 DTMC 저장소 권한 추가 필요

## 다음 할 일

### v0.1.11 사용자 시험(4090)
1. [AUTO] — 품질 '기본'(shift3 + 4B + 대체 변환부)과 '고품질'(XL + 대체 변환부) 둘 다 한 번씩.
2. [비교 시험] → 스타일 샘플 10곡 → 쓸 만한 스타일 알려 주기(품질을 바꿔 한 번 더 돌리면 다른 품질로 10곡).
3. 문제 = `내 문서\DTMC\logs` 의 dtmc.log · engine.log.

### 시험 뒤
- 쓸 만한 스타일을 [AUTO] 에 '스타일로 고르기'로 넣기(악기 고르기와 나란히). 스타일 지시문 다듬기.
- 설정 화면 두 줄 배치는 DTME v0.17.2 뒤에(아래 메모).
- 용도 = 다이빙 영상 BGM 가끔 → Suno 를 필요할 때 구독하는 길도 열려 있음(미정). 더 큰 음질 수단 = LoRA 직접 학습 · '다시 연주하기'.

### 지난 시험 결과(11/18)
- v0.1.10: 대체 변환부 = 실제 연주처럼(채택) · 16단계 = 먹먹함 · XL + 작곡 4B = 실패.
- v0.1.9: shift3 · XL + DCW 끔이 좋음(XL 의 새는 소리 = DCW) · 가사 칸/악기 강조/작곡 보수/XL SFT = 효과 없음.
- v0.1.8: 기본 < shift3 · XL = 풍부하나 새는 소리.
- v0.1.7: 새 지시문 = 훨씬 양호 · 음색 참고 = 조금 나음 · 원곡 이해 = 엉킴(보류) · 옛 방식(커버) = 목표와 안 맞음.

### 메모(사용자)·미정
- **설정 화면을 DTME 처럼 다 펼친 두 줄로(사용자 11/18 16:02 "기다렸다 하기")**: DTME v0.17.2 의 자동 창 두 줄 배치(1400×720, 스크롤 없음 — DTME HANDOFF ② B안, 11/18 현재 코드 미반영)가 **실제 코드로 나온 뒤** 그 코드·수치·접기 동작을 그대로 옮긴다. 먼저 만들지 않음. DTMC 배치안: 왼쪽 원곡 목록(폭 약 400) · 가운데 분석 + 출력 · 오른쪽 생성 · 맨 위 요약 줄. [AUTO] 뒤 진행·결과 화면은 지금 그대로. 옮길 때 DTME `app/dtme/ui_autowin.py` 참고(공개 저장소라 읽기 가능).
- 파일 이름에 반영 %·악기·제목·길이(11/18 14:59) → 반영 % 가 없어져 `제목_악기_번호_길이` 유지(사용자 확인 11/18 15:48 "한번에 ㄱㄱ", 순서 변경 요청 없음).
- 지시문 직접 입력·스타일 고르기·보낸 지시문 미리보기: 상의만 함, 모델 비교 뒤.
- 내장 음색 참고 음원(권리 문제 없는 녹음): 급하지 않음.
- 악기 목록 최종 확정: [악기 시험]이 새 지시문으로 바뀌었으니 다시 들어 보고.

### 결정
- 엔진 켜기 = 필요할 때(첫 [AUTO]·[악기 시험]·[비교 시험]). 프로그램 끄면 엔진도 끔.
