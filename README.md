# Gil's FlowDesk

**브라우저 기반 비주얼 AI 워크플로우 에디터**

다수의 Claude AI 에이전트를 노드로 배치하고, 연결선으로 워크플로우를 구성하여 자동 연쇄 작업을 수행하는 시스템입니다. `claude -p` (print 모드)를 통해 Claude Code CLI와 직접 통신하며, 별도의 터미널이나 tmux 없이 순수 웹 브라우저에서 모든 것을 제어합니다.

```
브라우저 캔버스 (노드 연결/실행/메모)
    ↓ HTTP API
Python 서버 (server.py:8888)
    ↓ subprocess (stdin)
claude -p --dangerously-skip-permissions
    ↓ Anthropic API
Claude AI → 응답 → 다음 노드로 자동 전달
```

## 주요 기능

### 캔버스 워크플로우
- **무한 캔버스** — 마우스 드래그로 팬, 휠로 줌, 전체보기
- **4가지 노드 타입** — 🤖 Agent (AI 실행), 📝 Memo (데이터), 📎 Input (입력), ⚡ Trigger (트리거)
- **노드 연결** — 드래그로 연결선 생성, `{{노드명}}` 변수로 이전 결과 자동 주입
- **노드 박스 드롭 연결** — 포트에 정확히 맞추지 않아도 노드 위에 놓으면 자동 연결
- **워크플로우 실행** — 전체 실행 또는 개별 노드 실행, 자동 순차/병렬 처리
- **실행 모드** — 대화전용 (💬) / CLI 도구 사용 (🔧) 모드 전환
- **Output 편집** — 실행 결과를 직접 수정하여 다음 노드 입력 최적화
- **토큰 제한 경고** — 23,000자 초과 시 접힌 노드에 빨간 펄싱 경고, 실행 전 확인
- **치환 미리보기** — `{{변수}}`가 실제 내용으로 치환된 결과를 색상 구분하여 표시

### 메모 시스템
- **노트패드식 탭** — 여러 메모를 브라우저 탭처럼 관리, 가로 스크롤
- **자동 저장** — 1초 디바운스로 DB에 자동 저장, 수동 저장 불필요
- **날짜별 폴더** — 저장 시 자동으로 날짜 폴더(📅 2026-04-11)에 분류
- **즐겨찾기** — 📌 고정 메모를 상단 네비게이션에 칩으로 표시, ⭐ 즐겨찾기 팝업
- **폴더 트리** — 아이콘/색상 커스터마이징, 접기/펼치기, 일괄 삭제
- **메모 ↔ 노드 전환** — 메모를 노드로 즉시 생성, 노드 Input/Output을 메모로 전환

### 캔버스 이미지
- **이미지 배치** — 캔버스에 이미지를 드래그앤드롭으로 배치
- **노드 전환** — 이미지에 Agent/Memo/Input 타입을 부여하면 연결점 생성, 노드처럼 사용
- **이미지 첨부** — 캔버스 이미지를 다른 Agent 노드에 이미지 인풋으로 전송
- **연결선 기반 첨부** — 이미지와 연결된 모든 노드에 자동 이미지 첨부

### 프로젝트 관리
- **프로젝트 저장/불러오기** — SQLite DB에 저장, 즐겨찾기, 이름 변경
- **임시 자동 백업** — 작업 중 자동 임시 저장, 30일 자동 정리
- **일괄 삭제** — 체크박스로 여러 프로젝트/메모 한번에 삭제
- **실행 기록** — 모든 노드 실행 이력을 DB에 기록

### UI/UX
- **다크 테마** — Nord 색상 기반의 시각적으로 구분된 UI
- **색상 코딩** — 기능별 버튼 색상 구분 (실행=초록, 정지=빨강, 메모=노랑, 시스템=보라, 이미지=청록)
- **사이드바 압축** — Input/Output이 사이드바의 90% 이상 차지, 도구 버튼은 아이콘으로 축약
- **리사이즈 구분선** — 사이드바/노드 내 Input↔Output 영역 드래그 조절
- **확대 팝업** — Input/Output을 전체화면으로 확대, HTML 렌더링, 메모 전환

## 아키텍처

```
[캔버스 UI]  ──HTTP──  [server.py:8888]  ──subprocess──  [claude -p]
                              │                               │
                              ├─ /api/node-exec (실행)         ├─ stdin으로 프롬프트 전달
                              ├─ /api/node-check (완료 확인)    ├─ stdout로 응답 수신
                              ├─ /api/project/* (저장/불러오기)  └─ --dangerously-skip-permissions
                              ├─ /api/memo/* (메모 CRUD)
                              ├─ /api/upload (이미지 업로드)
                              └─ /api/folder/* (폴더 관리)
```

**핵심**: `claude -p` (print 모드)로 터미널 UI 없이 텍스트만 주고받습니다. tmux나 별도 터미널이 불필요합니다.

## 설치

### 요구사항

| 항목 | 버전 |
|------|------|
| Windows 10/11 | WSL2 지원 |
| WSL Ubuntu | 24.04 권장 |
| Python 3 | 3.10+ |
| Node.js | 18+ (Claude CLI용) |
| Anthropic 계정 | Claude Max 또는 API Key |

### 1단계: WSL + 프로젝트 클론

```bash
# WSL Ubuntu에서
git clone https://github.com/biomech-gil/gils-flowdesk.git
cd gils-flowdesk
```

### 2단계: 자동 설치

```bash
bash setup.sh
```

setup.sh가 자동 설치하는 것:
- tmux, python3, curl, lsof (apt)
- nvm + Node.js 22 LTS
- Claude Code CLI (`@anthropic-ai/claude-code`)

### 3단계: Claude 인증

```bash
claude  # 첫 실행 시 Anthropic 로그인 (1회)
```

### 4단계: 실행

**방법 A — 직접 실행:**
```bash
cd ~/gils-flowdesk
python3 server.py
# 브라우저에서 http://127.0.0.1:8888 접속
```

**방법 B — Windows 바탕화면 bat:**

`claude_flow.bat` 파일을 바탕화면에 생성:
```bat
@echo off
chcp 65001 >con
title Gil's FlowDesk
wsl -d Ubuntu -- bash ~/gils-flowdesk/launch-server.sh
timeout /t 4 /nobreak >con
start "" http://127.0.0.1:8888
pause
```

### 5단계: 사용

1. 브라우저에서 `http://127.0.0.1:8888` 접속
2. 우클릭으로 Agent/Memo/Input 노드 생성
3. 노드 연결 → Input에 프롬프트 작성 → ▶ 실행
4. 워크플로우 저장 (💾)

## 트러블슈팅

### claude 명령을 못 찾음
```
해결: source ~/.nvm/nvm.sh && which claude
서버가 자동으로 nvm/시스템 경로를 감지합니다.
```

### 긴 프롬프트 타임아웃
```
원인: claude -p는 약 23,000자(한국어) 이상에서 응답 지연 가능
해결: 메모를 분할하고 중간 요약 Agent를 넣어 단계별 처리
경고: 23,000자 초과 시 자동 경고 팝업 표시
```

### localhost 접속 안 됨
```
원인: localhost가 IPv6(::1)로 해석되어 서버(IPv4)에 연결 안 됨
해결: 127.0.0.1:8888 으로 접속
```

### 포트 충돌
```
해결: lsof -i :8888 로 확인 후 kill, 서버에 allow_reuse_address 적용됨
```

## 파일 구조

```
gils-flowdesk/
├── canvas.html          ← 메인 UI: 노드 캔버스 + 메모 시스템
├── chat.html            ← 채팅 UI: 노드별 대화 인터페이스
├── server.py            ← Python HTTP 서버 + Claude CLI 연동
├── canvas.db            ← SQLite DB (프로젝트/메모/실행기록)
├── setup.sh             ← 자동 설치 스크립트
├── launch-server.sh     ← 서버 기동 스크립트 (tmux 백그라운드)
├── uploads/             ← 업로드된 이미지 저장소
└── README.md            ← 이 문서
```

## API 엔드포인트

| Method | Path | 설명 |
|--------|------|------|
| POST | `/api/node-exec` | 노드 실행 (claude -p, stdin) |
| GET | `/api/node-check?nodeId=` | 실행 완료 확인 + 결과 조회 |
| POST | `/api/chat` | 채팅 메시지 전송 |
| POST | `/api/project/save` | 프로젝트 저장 |
| GET | `/api/project/load?id=` | 프로젝트 불러오기 |
| GET | `/api/project/list` | 프로젝트 목록 |
| POST | `/api/memo/save` | 메모 저장 |
| GET | `/api/memo/list` | 메모 목록 |
| GET | `/api/memo/pinned` | 고정 메모 목록 |
| POST | `/api/folder/save` | 폴더 생성/수정 |
| GET | `/api/folder/list` | 폴더 목록 |
| POST | `/api/upload` | 이미지 업로드 |

## 향후 계획

- Docker 컨테이너화 (시놀로지 NAS 배포)
- 토큰 갱신 관리 UI
- 워크플로우 템플릿 공유
- 실시간 스트리밍 출력

## 라이선스

MIT
