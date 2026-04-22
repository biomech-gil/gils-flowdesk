# Gil's FlowDesk

**브라우저 기반 멀티 에이전트 워크플로우 · 문서 편집 · 미디어 허브**

Claude와 Gemini 등 여러 AI CLI를 노드로 배치·연결해 자동 연쇄 작업을 구성하고, 동시에 docx/hwp 문서 인라인 편집, YouTube 검색/자막 생성, 미디어 다운로드, Excel 시트 작성 등 **개인 연구실 수준의 복합 작업**을 단일 브라우저 캔버스에서 수행합니다.

> 시놀로지 NAS에 Docker로 배포하고 여러 PC에서 접속하는 것을 기본 사용 시나리오로 설계되었습니다. 순수 웹 브라우저에서 모든 것을 제어하며 별도의 터미널이나 tmux 없이 `claude -p` / `gemini -p` print 모드로 통신합니다.

## 시스템 개요

```
┌──────────────── 브라우저 (canvas.html) ────────────────┐
│  무한 캔버스 · 노드 워크플로우 · 문서/시트/비디오 인라인  │
│  YouTube 검색 · 메모 시스템 · 파일 패널 · 인증 캐시       │
└─────────────────────────┬──────────────────────────────┘
                          │ HTTP JSON API
                          ▼
┌──────────────── server.py (Python, :8888) ─────────────┐
│  PostgreSQL/SQLite · 파일 I/O · 버전 관리               │
│  claude -p · gemini -p · yt-dlp · ffmpeg · whisper     │
│  openpyxl · mammoth · googleapiclient                  │
└────────────────────────────────────────────────────────┘
```

## 노드 타입

| 타입 | 아이콘 | 설명 |
|---|---|---|
| Agent (Claude) | 🤖 | Claude Code CLI 실행 — 대화 전용 또는 도구 사용 모드 |
| Agent (Gemini) | ✨ | Gemini CLI 실행 — 동일하게 모드 전환 가능 |
| Memo | 📝 | 정적 텍스트 · 프롬프트 템플릿 · 즐겨찾기 |
| Input | 📎 | 외부 입력 · 수동 편집 · 트리거 대상 |
| Trigger | ⚡ | 워크플로우 시작점 |
| Downloader | 📥 | 여러 URL 일괄 → mp4/mp3/이미지/자막 다운로드 |
| Sheet | 📊 | 엑셀식 그리드 · xlsx import/export · 시트 연산 |
| Document | 📄 | docx (SuperDoc) · hwp (rhwp) · 텍스트/파일카드 인라인 편집 + 버전관리 |

## 핵심 기능

### 🔗 워크플로우
- **연결선 기반 자동 연쇄** — 드래그로 연결, `{{노드명}}` / `{{@메모}}` 변수 치환, 23,000자 토큰 경고
- **4방향 포트** — 상하좌우 어느 쪽으로도 연결. 노드 박스에 드롭하면 자동 연결
- **영역(Region)** — 드래그로 사각형 영역 생성, 여러 노드 그룹화
- **영역 스크롤 박스 🔭** — 영역을 스크롤 뷰포트로 변환 → 20페이지 문서를 세로로 펼쳐놓고 휠로 훑기
- **영역 실행 오케스트레이션 🔢 🎬** — 영역마다 실행 순서 부여 → 순서대로 영역별 DAG 실행, 📊 진행 패널에서 실시간 모니터링
- **치환 미리보기** — `{{변수}}` 가 실제 내용으로 치환된 결과를 색상 구분 표시
- **Output 수동 편집** — 실행 결과를 직접 다듬어 다음 노드 입력 최적화

### 📄 문서 (Document 노드)
- **docx 인라인 편집** — SuperDoc 엔진, A4 리본 편집
- **hwp 인라인 편집** — rhwp 엔진 (CDN preload, 빈 화면 자동 재주입)
- **Time Machine 버전 관리** — 저장마다 `.versions/` 자동 스냅샷 + 씨이어링 (24h 전부 → 시간당 → 일당 → 주당 → 월당)
- **🏁 마일스톤** — 임의 시점 이름 붙은 영구 스냅샷
- **자동 브로드캐스트 리로드** — 같은 파일을 가진 다른 노드들을 저장 시점에 자동 리로드 (스크롤 위치 보존)
- **외부 수정 감지** — mtime 기반, 충돌 시 덮어쓰기/버전 보관 선택
- **HWP → DOCX 변환** — 서버 측 자동 변환

### 📊 시트 (Sheet 노드)
- **엑셀식 그리드** — 행/열 추가, 셀 편집, 정렬
- **xlsx I/O** — openpyxl 기반 import/export, 다운로드 버튼
- **출력 포맷** — Markdown/CSV/JSON/XLSX 선택

### 🎬 미디어
- **캔버스 비디오 임베드** — YouTube/Vimeo/TikTok/Instagram/직접 mp4 URL 자동 감지
- **YouTube 검색** — Data API v3 · hot_score 분석 (참여율/일평균 조회수/경과일) · 체크박스 다중 선택 → 격자 배치
- **자막 자동 생성** — YouTube 네이티브 API 우선, 없으면 faster-whisper CPU 폴백, SRT → WebVTT 변환 후 `<track>` 주입
- **프레임 캡처** — yt-dlp `--download-sections` 로 3초 세그먼트 + ffmpeg 추출
- **북마크** — 특정 시각에 라벨 저장, 타임라인 점프
- **다운로드 노드 📥** — 여러 URL 일괄 mp4/mp3/이미지 + 자막을 프로젝트 폴더에 저장

### 🔐 인증/계정
- **Claude 다계정** — `.credentials.json` 파일 업로드 · 토큰 붙여넣기 · 웹 OAuth · 자동 로테이션 (round-robin / 우선순위 / 수동)
- **Gemini 다계정** — API 키 · OAuth JSON · 격리된 홈 디렉토리
- **🔄 인증 자동 복구** — 계정 업로드 시 브라우저 IndexedDB에 캐시, 서버 인증 실패 감지 시 조용히 재업로드 후 재점검
- **YouTube Data API 키** — 설정 UI에서 저장 (DB 우선, env 폴백)
- **인증 헬스 배지** — 상단 실시간 표시, 클릭하면 상세/재점검

### 📁 프로젝트/파일
- **프로젝트 = 폴더 모델** — `/synology/{년도}/{YYYYMMDD}_이름/` 고정 구조, 폴더 자체가 프로젝트
- **Time Machine 임시 작업** — 저장 안 한 작업은 `/synology/_temp/` → 2일 후 휴지통 → 5일 후 영구 삭제
- **폴더 탐색 패널** — 좌측 상단 실시간 파일 리스트 (docs/images/videos/audio/other 그룹), 드래그로 캔버스에 노드 생성
- **DSM 딥링크** — 외부 DSM 파일 스테이션으로 바로 열기 (호스트 경로 ↔ 컨테이너 경로 자동 변환)
- **시놀로지 브릿지** — `/synology` bind mount 로 컨테이너 루트 밖 임의 폴더 접근

### 🧠 메모 시스템
- 브라우저 탭식 다중 메모, 1초 디바운스 자동 저장
- 날짜별 폴더 자동 분류 (📅 2026-04-11)
- 📌 고정 메모 상단 네비게이션 칩, ⭐ 즐겨찾기 팝업
- 폴더 트리 아이콘/색상 커스터마이징, 접기/펼치기, 일괄 삭제
- 메모 ↔ 노드 양방향 전환

### 🎨 UI/UX
- Nord 기반 다크 테마, 기능별 색상 코딩
- 캔버스 배경 클릭으로 노드+CE 통합 선택 해제
- 캔버스 요소 (CE): 영역, 텍스트, 이미지, 비디오, 체크리스트, 미니 표
- 사이드바 Input/Output 90%+ 차지, 리사이즈 가능한 구분선
- 확대 팝업: Input/Output 전체화면 HTML 렌더링, 메모 전환
- 4방향 포트 · 드롭 연결 · 방향 인식 곡선 베지어

## 아키텍처

```
┌────────────────────── 브라우저 (canvas.html) ───────────────────┐
│  ├─ 노드/CE 캔버스 + 연결선 SVG                                 │
│  ├─ 메모 시스템 (탭·폴더·핀·즐겨찾기)                            │
│  ├─ 문서 엔진 로더 (SuperDoc / rhwp / mammoth / plain)          │
│  ├─ 미디어 (YouTube IFrame, 비디오 오버레이, WebVTT 자막)        │
│  └─ IndexedDB 인증 캐시 + localStorage 설정                      │
└──────────────────┬──────────────────────────────────────────────┘
                   │ HTTP JSON API
                   ▼
┌────────────────────── server.py (Python stdlib) ────────────────┐
│  :8888 HTTPServer                                                │
│  ├─ PostgreSQL (메인) / SQLite (폴백) 이중 지원 · _pg_adapt_sql   │
│  ├─ /api/node-exec → claude -p / gemini -p subprocess            │
│  ├─ /api/doc/* → docx/hwp/txt 바이너리 I/O + 버전관리/씨이어링    │
│  ├─ /api/sheet/* → openpyxl xlsx import/export                   │
│  ├─ /api/media/* → yt-dlp + ffmpeg (다운로드/프레임/자막)         │
│  ├─ /api/youtube/search → Data API v3 + hot_score                │
│  ├─ /api/auth/health → 모든 계정 병렬 인증 테스트 (ThreadPool)    │
│  └─ /api/project/* /memo/* /folder/* /upload /settings/*          │
└──────────────────┬──────────────────────────────────────────────┘
                   │
   ┌───────────────┼──────────────┬─────────────────┬─────────────┐
   ▼               ▼              ▼                 ▼             ▼
 claude -p      gemini -p     yt-dlp+ffmpeg    faster-whisper   openpyxl
 (Anthropic)   (Google AI)    (미디어)          (CPU 자막)       (엑셀)
```

## 시놀로지 NAS Docker 배포 (권장)

> 자세한 배포 가이드·포트 선택·외부 접속·트러블슈팅: [📖 DEPLOYMENT.md](./DEPLOYMENT.md)

### 초기 1회 설정
```bash
# 시놀로지 SSH 접속 후:

# 1. 필요한 모든 폴더를 uid 1000 권한으로 일괄 생성
sudo bash setup-synology.sh
#  → /volume1/FlowDesk/{db, workspace, creds, gemini-creds, uploads, whisper-cache}

# 2. 앱 파일 배치
mkdir -p /volume1/docker/gils-flowdesk
# Dockerfile, docker-compose.yml, server.py, canvas.html, .env.example 등 업로드

# 3. .env 작성
cd /volume1/docker/gils-flowdesk
cp .env.example .env
# DB_PASSWORD (필수) · PORT (기본 9090) · YOUTUBE_API_KEY (선택) · WHISPER_MODEL (small) 등

# 4. 빌드 + 실행
docker-compose up -d
```

### 첫 로그인
- 브라우저: `http://시놀로지IP:포트`
- ⚙️ 설정 → 🔑 Claude / Gemini 인증 업로드 → 사용 시작
- (선택) 🎬 YouTube Data API 키 등록

### 외부 접근
시놀로지 포트 포워딩 또는 DDNS + 리버스 프록시(HTTPS) 권장. DSM 딥링크용 URL 은 ⚙️ 설정에서 DDNS 주소로 지정.

## 로컬 설치 (WSL Ubuntu)

### 요구사항
| 항목 | 버전 |
|------|------|
| WSL2 Ubuntu | 24.04 권장 |
| Python 3 | 3.10+ |
| Node.js | 18+ (Claude/Gemini CLI) |

### 자동 설치
```bash
git clone https://github.com/biomech-gil/gils-flowdesk.git
cd gils-flowdesk
bash setup.sh      # tmux, nvm, node22, claude CLI 자동 설치
claude             # 1회 Anthropic 로그인
python3 server.py  # http://127.0.0.1:8888
```

## 자주 쓰는 단축키
| 단축키 | 동작 |
|---|---|
| Alt+S | 프로젝트 저장 |
| Alt+A | 전체 실행 |
| Ctrl+Z / Ctrl+Y | 실행취소 / 재실행 |
| Ctrl+휠 | 캔버스 줌 (영역 밖) / 영역 폰트 크기 (라벨 위) |
| ESC | 모달/모드 종료 |

## 파일 구조

```
gils-flowdesk/
├── canvas.html          ← 메인 UI (단일 파일, 10,000+ lines)
├── chat.html            ← 채팅 전용 UI
├── server.py            ← Python HTTP 서버 (6,000+ lines)
├── Dockerfile           ← 빌드 이미지 (Python + Node + ffmpeg + yt-dlp)
├── docker-compose.yml   ← Postgres + app 서비스 정의
├── setup-synology.sh    ← 시놀로지 폴더/권한 초기 설정
├── .env.example         ← 환경변수 템플릿
├── DEPLOYMENT.md        ← 상세 배포 가이드
└── README.md            ← 이 문서
```

## 주요 API 엔드포인트

| Method | Path | 설명 |
|---|---|---|
| POST | /api/node-exec | 노드 실행 (Claude/Gemini subprocess) |
| GET  | /api/node-check | 실행 결과 폴링 |
| POST | /api/project/save | 프로젝트 저장 (폴더 = 프로젝트) |
| GET  | /api/project/load | 프로젝트 불러오기 |
| POST | /api/doc/read | 문서 바이너리 + mtime |
| POST | /api/doc/write | 문서 저장 (외부 수정 감지 + 자동 스냅샷) |
| GET  | /api/doc/versions | 버전 목록 |
| POST | /api/doc/milestone | 영구 스냅샷 |
| POST | /api/doc/hwp-to-docx | HWP → DOCX 변환 |
| POST | /api/sheet/import | xlsx → JSON |
| POST | /api/sheet/export | JSON → xlsx |
| POST | /api/media/download | yt-dlp 다운로드 |
| POST | /api/media/extract-frame | 특정 시각 프레임 |
| POST | /api/media/subtitle | 자막 생성 (YouTube → Whisper) |
| POST | /api/youtube/search | YouTube Data API v3 + hot_score |
| GET  | /api/auth/health | 전체 계정 인증 상태 |
| POST | /api/claude/accounts/* | Claude 계정 CRUD + 로테이션 |
| POST | /api/gemini/accounts/* | Gemini 계정 CRUD |
| GET/POST | /api/settings/* | system_settings (YouTube 키 등) |
| POST | /api/memo/* /folder/* | 메모/폴더 CRUD |
| POST | /api/upload | 이미지 업로드 |

## 트러블슈팅

### 시놀로지 Bind mount 실패
빌드 시 "`/volume1/FlowDesk/xxxx` does not exist" → `sudo bash setup-synology.sh` 재실행.

### claude/gemini 명령 못 찾음 (로컬)
`source ~/.nvm/nvm.sh && which claude` · 서버가 자동으로 nvm 경로 감지.

### 긴 프롬프트 타임아웃
23,000자 초과 시 펄싱 경고 · 메모 분할 + 중간 요약 Agent로 단계별 처리 권장.

### localhost 접속 안 됨
IPv6 해석 문제 · `127.0.0.1:8888` 로 접속.

### rhwp 첫 드래그 빈 화면
라이브러리 초기화 레이스 · 자동 2단계 재주입(600ms/1500ms)이 시도, 실패 시 노드의 🔄 리로드.

### 인증 실패 배지
토큰 만료 · ⚙️ 설정 → 🔄 자동 복구 토글 ON 이면 IndexedDB 캐시로 재업로드 시도. 그래도 실패 시 재로그인.

### 같은 파일 여러 노드 편집 충돌
한 노드 저장 → 나머지 노드 자동 리로드. 다른 노드도 편집 중이면 그 노드는 스킵 + 경고 토스트 (수동 🔄 로 해결).

### 포트 충돌 (로컬)
`lsof -i :8888` 로 확인 후 kill · 서버에 `allow_reuse_address` 적용돼 있음.

## 라이선스

MIT
