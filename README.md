# Claude Node Canvas — tmux + Claude Code CLI 노드 워크플로우 에디터

브라우저 기반 비주얼 노드 에디터에서 다수의 Claude AI 인스턴스를 연결하여 자동 연쇄 작업을 수행하는 시스템입니다.  
Google Opal과 유사한 노드 캔버스 UI로, `claude -p` (print 모드)를 통해 Claude Code CLI와 통신합니다.

```
브라우저 캔버스 (노드 에디터)
    ↕ HTTP API
Python 서버 (server.py)
    ↕ subprocess
Claude Code CLI (claude -p)
    ↕ Anthropic API
Claude AI
```

## 주요 기능

### 캔버스 (메인 — localhost:8888)
- **무한 캔버스** — 마우스 드래그로 팬, 휠로 줌, 전체보기 버튼
- **노드 유형 4가지** — 🤖 Agent (AI 실행), 📝 Memo (메모), 📎 Input (데이터), ⚡ Trigger (트리거)
- **노드 연결** — 우측 포트 → 좌측 포트 드래그로 연결, `{{노드명}}` 변수 자동 삽입
- **워크플로우 실행** — 연결 순서대로 자동 연쇄 실행, 이전 노드 출력이 다음 노드 입력에 자동 삽입
- **노드별 모드** — 💬 대화전용 (도구 차단) / 🔧 CLI (파일 읽기/쓰기 가능) 토글
- **노드 분할** — 1개 입력으로 N개 노드 동시 생성 (병렬 작업)
- **다중 선택** — Ctrl+클릭 개별 선택, Ctrl+드래그 범위 선택
- **접기/펼치기** — 더블클릭, 우클릭 메뉴, 전체 접기/펼치기
- **정렬** — 연결 방향 기반 레벨 정렬 (BFS)
- **저장/불러오기** — JSON 파일로 워크플로우 영구 저장
- **상태 유지** — 탭 전환 시 localStorage로 상태 보존

### 채팅 (localhost:8888/chat.html)
- 각 노드별 개별 대화 가능
- 캔버스 워크플로우 기록 + 채팅 기록 통합 표시
- 대화 맥락 유지 (히스토리 전체를 프롬프트에 포함)

### 컨트롤러 (localhost:8888/index.html)
- tmux 패널 분할/레이아웃 프리셋
- 전체 패널 동시 명령 전송
- Claude Auto 버튼

## 아키텍처

```
[캔버스 UI] ──HTTP──→ [server.py:8888] ──subprocess──→ [claude -p --dangerously-skip-permissions]
                              │                                    │
                              ├─ /api/node-exec (실행 요청)         ├─ 프롬프트 전달
                              ├─ /api/node-check (완료 확인)        ├─ stdout → 파일 저장
                              ├─ /api/chat (대화)                   └─ 종료 시 done 파일 생성
                              ├─ /api/workflow/* (저장/불러오기)
                              └─ /api/status (tmux 상태)
```

**핵심**: `claude -p` (print 모드)를 사용하므로 터미널 UI 없이 순수 텍스트만 주고받습니다.  
tmux는 초기 환경 구성용이며, 실제 AI 통신은 서버의 백그라운드 스레드에서 `subprocess.run(["claude", "-p", ...])` 으로 직접 수행합니다.

## 새 PC에 설치하기

### 사전 요구사항

| 항목 | 비고 |
|------|------|
| Windows 10/11 | 빌드 19041+ |
| Windows Terminal | 보통 기본 설치됨 |
| WSL2 | Ubuntu-24.04 |
| Anthropic 계정 | Claude Max 또는 API 키 |

### 1단계: WSL Ubuntu 설치

**관리자 cmd**에서:

```cmd
wsl --install -d Ubuntu-24.04
```

Ubuntu 창에서 **사용자명/비밀번호 설정**.

> OOBE에서 멈추면 → PC 재부팅 후 다시 시도

### 2단계: 프로젝트 클론

```cmd
git clone https://github.com/biomech-gil/tmux-config-korean.git
cd tmux-config-korean
```

### 3단계: WSL에 파일 복사 + 자동 설치

#### 로컬 드라이브(C:, D:)에 클론한 경우:

```cmd
wsl -d Ubuntu-24.04 -- bash -c "cd /mnt/c/경로/tmux-config-korean && bash setup.sh"
```

#### 네트워크 드라이브(Z: 등 NAS)에 클론한 경우:

WSL은 네트워크 드라이브를 `/mnt/`로 마운트하지 않습니다. 수동 복사가 필요합니다:

```cmd
:: cmd에서 실행 (반드시 C: 사용자 폴더로 이동 후)
cd C:\Users\%USERNAME%

:: WSL에 디렉토리 생성
wsl -d Ubuntu-24.04 --cd ~ -- bash -c "mkdir -p ~/tmux-controller"

:: 파일 복사 (각 줄 실행)
type Z:\경로\tmux-config-korean\server.py  | wsl -d Ubuntu-24.04 --cd ~ -- bash -c "cat > ~/tmux-controller/server.py"
type Z:\경로\tmux-config-korean\index.html | wsl -d Ubuntu-24.04 --cd ~ -- bash -c "cat > ~/tmux-controller/index.html"
type Z:\경로\tmux-config-korean\canvas.html| wsl -d Ubuntu-24.04 --cd ~ -- bash -c "cat > ~/tmux-controller/canvas.html"
type Z:\경로\tmux-config-korean\chat.html  | wsl -d Ubuntu-24.04 --cd ~ -- bash -c "cat > ~/tmux-controller/chat.html"
type Z:\경로\tmux-config-korean\start.sh   | wsl -d Ubuntu-24.04 --cd ~ -- bash -c "cat > ~/tmux-controller/start.sh"
type Z:\경로\tmux-config-korean\setup.sh   | wsl -d Ubuntu-24.04 --cd ~ -- bash -c "cat > ~/tmux-controller/setup.sh"
type Z:\경로\tmux-config-korean\tmux.conf  | wsl -d Ubuntu-24.04 --cd ~ -- bash -c "cat > ~/tmux-controller/tmux.conf"

:: setup.sh 실행
wsl -d Ubuntu-24.04 --cd ~ -- bash -c "cd ~/tmux-controller && bash setup.sh"
```

**setup.sh가 자동으로 설치하는 것:**
- tmux, python3, curl, lsof (apt)
- nvm + Node.js 22 LTS
- Claude Code CLI (`@anthropic-ai/claude-code`)
- `~/.tmux.conf` 심볼릭 링크

### 4단계: Claude 최초 인증

```cmd
wsl -d Ubuntu-24.04 --cd ~ -- bash -c "source ~/.nvm/nvm.sh && claude"
```

Anthropic 계정으로 로그인합니다. 최초 1회만 필요합니다.

### 5단계: 바탕화면 bat 파일 생성

아래 내용을 `tmux_컨트롤러.bat`로 바탕화면에 저장합니다.  
**반드시 UTF-8 + CRLF 줄바꿈**으로 저장하세요.

```bat
@echo off
chcp 65001 >con
title tmux Web Controller

echo [i] Checking WSL Ubuntu-24.04...
wsl -d Ubuntu-24.04 -- echo [OK] Ubuntu ready
if errorlevel 1 (
    echo [!] Ubuntu-24.04 not found. Installing...
    wsl --install -d Ubuntu-24.04
    pause
    exit /b
)

echo [+] Starting server...
start "tmux-server" /min wsl -d Ubuntu-24.04 --cd ~ -- bash -c "cd ~/tmux-controller && bash start.sh"

timeout /t 3 /nobreak
start "" http://localhost:8888

echo [+] Opening tmux terminal...
start "" wt.exe wsl -d Ubuntu-24.04 --cd ~ -- bash -c "tmux attach -t main 2>/dev/null || (sleep 2 && tmux attach -t main)"

echo All started! Close this window when done.
pause
```

### 6단계: 실행

1. 바탕화면 `tmux_컨트롤러.bat` 더블클릭
2. 브라우저에서 `http://localhost:8888` 열림 (캔버스)
3. 우클릭 → Agent 노드 추가 → 프롬프트 입력 → ▶ 실행

## 트러블슈팅

### WSL 설치 멈춤

```
증상: OOBE에서 멈춤, wsl --unregister도 대기
해결: PC 재부팅 → 관리자 cmd → wsl --unregister Ubuntu-24.04 → wsl --install -d Ubuntu-24.04
```

### 네트워크 드라이브(Z:) WSL 접근 불가

```
증상: /mnt/z/ 경로 없음
원인: WSL2는 네트워크 드라이브 자동 마운트 안 함
해결: type 명령으로 파이프 복사 (설치 가이드 참고)
```

### Z: 드라이브에서 wsl 명령 에러

```
증상: CreateProcessParseCommon: Failed to translate Z:\...
해결: cd C:\Users\%USERNAME% 후 wsl 명령 실행, 또는 wsl --cd ~ 사용
```

### claude 명령을 못 찾음

```
증상: /mnt/c/.../npm/claude: exec: node: not found
원인: Windows npm의 claude가 WSL PATH에서 먼저 잡힘
해결: nvm 설치 후 tmux 세션 재시작 (tmux kill-server → start.sh)
      which claude → /home/사용자/.nvm/.../bin/claude 확인
```

### bat 파일 >nul이 >/dev/null로 변환됨

```
증상: 편집기가 Windows nul을 Unix /dev/null로 자동 변환
해결: >nul 리다이렉션 제거, 또는 >con 사용
```

### 노드 실행 후 출력이 안 잡힘

```
증상: Output 영역이 비어있음
원인: claude -p가 실행되었으나 done 파일 생성 전
해결: F12 콘솔에서 [NodeName] 로그 확인, 3초 간격 폴링 대기
```

## 파일 구조

```
tmux-config-korean/
├── canvas.html      ← 메인 UI: 노드 캔버스 에디터 (랜딩 페이지)
├── chat.html        ← 채팅 UI: 노드별 개별 대화
├── index.html       ← 컨트롤러 UI: tmux 패널 제어
├── server.py        ← Python HTTP 서버 + Claude 실행 엔진
├── start.sh         ← WSL 부트스트랩 (tmux + 서버 시작)
├── setup.sh         ← 신규 PC 자동 설치 스크립트
├── tmux.conf        ← tmux 설정 (Nord 테마, Ctrl+a 프리픽스)
├── tmux_사용법.md    ← tmux 단축키 가이드
└── README.md        ← 이 문서
```

**WSL 설치 위치**: `~/tmux-controller/`

## API 엔드포인트

| Method | Path | 설명 |
|--------|------|------|
| POST | `/api/node-exec` | 노드 실행 (claude -p 백그라운드) |
| GET | `/api/node-check?nodeId=` | 실행 완료 확인 + 출력 읽기 |
| POST | `/api/chat` | 채팅 메시지 전송 (히스토리 포함) |
| GET | `/api/chat-check?chatId=` | 채팅 응답 확인 |
| GET | `/api/chat-history?chatId=` | 채팅 히스토리 조회 |
| POST | `/api/workflow/save` | 워크플로우 저장 |
| GET | `/api/workflow/load?id=` | 워크플로우 불러오기 |
| GET | `/api/workflow/list` | 워크플로우 목록 |
| POST | `/api/reset-session` | tmux 세션 완전 리셋 |
| POST | `/api/add-pane` | tmux 패널 추가 |
| GET | `/api/status` | tmux 세션/패널 상태 |
| POST | `/api/send-command` | tmux 패널에 명령 전송 |
| POST | `/api/preset` | 프리셋 레이아웃 적용 |

## 라이선스

MIT
