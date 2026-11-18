# HANDOFF — DTMC (Desktop Music Composer)

> 새 대화에서 이어서 작업할 때 이 파일을 기준으로 한다.
> 마지막 갱신: 2026-11-18 · **현재 = v0.1.3 → 사용자 4090 시험 중(v0.1.2 에서 엔진 설치까지 됨, '엔진 준비 0%' 멈춤 → v0.1.3 수정)**

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

## 변경 이력
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
app/dtmc/prompt.py     악기 8 · 분위기 7 · 원곡 반영(낮음 = text2music / 중간 0.3 · 높음 0.6 = cover) → API 요청
app/dtmc/engine.py     엔진 설치·서버·요청(ACE_COMMIT 고정 — build.yml 과 같게)
app/dtmc/jobs.py       분석 스레드 · 생성 스레드(엔진 준비 → 분석 → 생성·저장) · 악기 시험 · 작업 기록 · 속도 기억(예상 시간)
app/dtmc/ui_start.py   시작 창(DTME Launcher 틀)
app/dtmc/ui_main.py    작업 창(DTME 자동 창 틀: 원곡 목록 + 단계 줄 분석·생성·출력 + 결과 줄 미리듣기)
app/dtmc/ui_engine.py  엔진 설치 창
app/dtmc/ui_widgets.py Seg · Multi · StageRow · StepBar · Grip 등(DTME 모양)
app/dtmc/ui_style.py · single.py · taskbar.py   DTME·MOSCO 에서 그대로 복사(손대지 말 것)
tests/mock_engine.py   가짜 ACE-Step API(사인파) — DTMC_ENGINE_URL 로 연결
tests/test_pipeline.py 분석 → 생성 → 5형식 저장 · 악기 시험 · 취소
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
- 원곡 반영 정도 기본값 '낮음' · "권리가 있는 음원만 넣으세요" 안내 · 생성 조건 작업 기록
- ffmpeg(GPL 빌드)·PySide6(LGPL) 고지 동봉
- 상업 판매 전 전문가 검토

## 결정 필요 사항
- [x] 시작 창 그림 — 사용자 제공(v0.1.2), 상업 배포 전 교체 검토
- [ ] 아이콘 — 임시(건반 그림) 유지, 나중에 따로
- [ ] 악기 목록 최종 확정 — 1단계 품질 시험 후

## 작업 방식 메모
- Claude 작업 환경에는 윈도우·GPU 없음 → 코드 작성·화면 캡처(offscreen)는 Claude, 실행 테스트는 사용자 PC
- 오류는 로그 파일로 남겨 공유(MOSCO처럼 `내 문서\DTMC\logs\` 제안)
- 빌드 올릴 때마다 Actions 링크 첨부(DTME·MOSCO 규칙과 같게)
- 이 세션에서 push 하려면 GitHub → Settings → Applications → Claude → Configure 에서 DTMC 저장소 권한 추가 필요

## 다음 할 일

### 결정
- 엔진 켜기 = **필요할 때(첫 [AUTO]·[악기 시험])** — 4090·노트북 모두(사용자 11/18 13:06). 미리 켜기는 안 함(지금 코드 그대로). 프로그램 끄면 엔진도 끔.
1. 사용자 4090 시험(위 '사용자 시험 순서') → 로그·음질 피드백 → v0.1.x 수정
2. 원곡 반영 강도(cover 0.3/0.6)·지시문 단어 조정
3. 노트북 측정(3단계)
