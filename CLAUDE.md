# Gil's FlowDesk — Claude 작업 지침

이 파일은 Claude Code(또는 다른 AI 도구)가 이 레포에서 작업할 때 자동 로드되는 운영 지침입니다. 사람도 읽을 수 있도록 마크다운으로 작성하되, AI 측 동작을 강제하기 위한 규칙들을 명시합니다.

---

## 🔑 인증 규칙 (가장 중요)

**Claude 인증은 무조건 setup-token으로 등록한다. `.credentials.json` 업로드는 사용하지 않는다.**

### 왜
- 사용자의 노트북 `~/.claude/.credentials.json`을 그대로 시놀로지에 업로드하면, OAuth refresh token rotation 때문에 노트북에서 갱신할 때마다 시놀로지의 토큰이 무효화 → "401 Invalid authentication credentials" 반복.
- setup-token(`sk-ant-oat01-...`)은 1년짜리 장기 토큰이고 갱신을 안 함. 같은 Claude.ai 계정의 다른 디바이스/세션과 충돌 안 함. 멀티 PC 환경에서 유일하게 안정.

### 어떻게
1. 사용자 PC cmd에서 `claude setup-token` 실행 → 브라우저 인증 → `sk-ant-oat01-XXX...` 토큰 표시
2. FlowDesk → ⚙️ 시스템 설정 → Claude Code 계정 관리 → **🔑 토큰만 붙여넣기** → 토큰 입력 → 저장 → 활성화
3. 만료/실패 시 같은 절차 반복 (옛 계정 삭제 후 새 토큰 등록)

### UI 정책 (2026-04-27 정리)
- Claude 쪽 ⚙️ 시스템 설정에는 **"🔑 setup-token 등록" 단일 버튼만 노출**. JSON 파일 업로드 / JSON 붙여넣기 / 웹 OAuth 버튼은 멀티 디바이스 충돌로 **삭제됨** (커밋 이력 참고).
- 인증 헬스 배지 상세 팝업의 "📎 인증서 업로드" 버튼은 Claude 의 경우 파일 picker 대신 **setup-token 텍스트 prompt**로 동작.
- 인증 실패 호소 → 즉시 setup-token 안내. JSON 업로드 절대 권하지 말 것.

### 코드 동작 (참고)
- `server.py` `get_claude_env_for_account` — credentials의 `refreshToken`이 빈 값이면 setup-token 모드로 인식, `CLAUDE_CODE_OAUTH_TOKEN` env var만 사용 (파일 안 만듦, `CLAUDE_CONFIG_DIR`/`HOME` 빈 dir 격리).
- `server.py` `_sync_claude_credentials` / `_claude_account_activate` — setup-token이 active일 때 글로벌 `~/.claude/.credentials.json` 명시적 삭제 (CLI가 OAuth로 오인하는 사고 방지).

---

## 🏗 시스템 아키텍처 (핵심만)

- **단일 파일 구조**: `server.py` (Python stdlib, ~7400줄) + `canvas.html` (~23000줄). 빌드 시스템 없음.
- **DB**: PostgreSQL(prod) / SQLite(local). `DB_TYPE` 환경변수로 분기, `_pg_adapt_sql`로 SQL 변환.
- **AI 실행**: `claude -p --dangerously-skip-permissions` / `gemini` CLI를 subprocess 호출.
- **시놀로지 배포**: `/volume1/FlowDesk/{db,workspace,creds,uploads,accts-runtime,gmini-accts-runtime,whisper-cache}` bind mount + `/synology` 전체 디스크 마운트.

새 기능 추가 시: `server.py` 메서드 + `do_GET`/`do_POST` 라우팅 등록 → `canvas.html` script 영역에 UI 로직. **별도 모듈/프레임워크/빌드 도입은 사용자 명시 요청 전엔 금지.**

---

## 📁 시놀로지 경로 정책 (하드 고정)

- **컨테이너 루트**: `SYNOLOGY_CONTAINER_ROOT = "/synology"` (= 호스트 `/volume1/00_Gils_Project` 또는 `/volume1`)
- **프로젝트 폴더**: `/synology/{YYYY}/{YYYYMMDD}_{이름}` — 코드 상수로 고정, DB 값으로 변경 불가.
- **임시 작업물**: `/synology/_temp/{YYYYMMDD}_{이름|Untitled}__{8id}/` → 2일 후 휴지통 → 5일 후 영구 삭제.
- **휴지통**: `/synology/.trash/_temp/{이름}__trashed-{ts}/`
- **정규화**: `_normalize_synology_path`가 호스트 prefix 중복(`/synology/00_Gils_Project/...`)을 자동으로 벗겨냄.

새 폴더/파일 경로 로직 작성 시:
1. `projects.work_dir`과 임시 폴더 경로는 **항상 `/synology/...`로만** DB 저장 (호스트 경로 금지).
2. 사용자 입력 경로는 `_normalize_synology_path` 통과 필수.
3. 저장 모달에 경로 입력창 추가 금지 — 정책이 사용자 손에 안 닿도록 의도적으로 고정.

---

## 🧩 멀티계정 인증 영구화 (2026-04-27 추가)

- `CLAUDE_RUNTIME_DIR=/app/accts-runtime` / `GEMINI_RUNTIME_DIR=/app/gmini-accts-runtime` / `CODEX_RUNTIME_DIR=/app/codex-accts-runtime` — 시놀로지 호스트에 bind mount해서 CLI가 토큰 갱신 시 쓴 파일을 영구 보존.
- `run_claude_safe` / `run_gemini_safe` / `run_codex_safe` 끝에서 `_persist_refreshed_*_creds`로 디스크의 갱신본을 DB로 역동기화.
- 부팅 시 `_sync_all_*_accounts`는 `force=False`로 호출 — 디스크에 살아있는 갱신본이 있으면 stale DB 토큰으로 덮어쓰지 않음.

## 🟢 Codex (ChatGPT 구독) 인증 규칙 (2026-04-28 추가)

**Codex 인증은 device-code 로그인을 우선한다. auth.json 업로드는 보조.**

### 왜
- ChatGPT 구독의 `~/.codex/auth.json`을 그대로 업로드하면 Claude credentials.json 사고와 동일한 메커니즘으로 멀티 디바이스 충돌 가능 (refresh_token rotation).
- `codex login --device-auth`는 컨테이너에서 독립 OAuth 세션을 만듦 → 본인 PC 의 codex 와 토큰 공유 안 함 → 멀티 PC 안전. 사실상 Codex 의 setup-token 등가물.

### 어떻게
1. **사전 1회**: chatgpt.com → 보안 설정 → "Sign in with Device Code" 활성화 (개인 계정은 본인이 켜면 됨).
2. FlowDesk → ⚙️ 시스템 설정 → Codex 계정 관리 → **🔐 device-code 로그인** → 표시된 URL/코드를 본인 PC 브라우저에서 입력 → 로그인 완료 → 자동 저장.
3. 백업: 같은 화면 "📂 auth.json 업로드 (보조)" 버튼 — 본인 PC `codex login` 결과 파일 업로드 (멀티디바이스 경고 표시 후 진행).

### UI 정책
- Codex 쪽 ⚙️ 시스템 설정에는 device-code 가 메인 버튼, auth.json 업로드는 보조 버튼으로만 노출.
- 인증 헬스 배지의 Codex 섹션도 device 로그인 버튼이 우선.
- API key 방식은 의도적으로 UI 노출 안 함 (사용자 정책: ChatGPT 구독만 사용).

### 코드 동작 (참고)
- `server.py` `_codex_login_start/_codex_login_status` — 임시 `CODEX_HOME` 에서 `codex login --device-auth` subprocess 실행, 완료 시 생성된 auth.json 을 `codex_accounts` 테이블로 영구 저장.
- `server.py` `get_codex_env_for_account` — 계정별 `CODEX_HOME=<acct>/.codex` 격리, apikey 면 `OPENAI_API_KEY` 도 설정.
- `build_codex_cmd` — `codex exec --skip-git-repo-check --color never -` (chatOnly: `--sandbox read-only`, 작업: `--full-auto`). 프롬프트는 stdin 전달. `codex exec` 는 비대화형이라 별도 승인 옵션 없음 (sandbox 가 도구 차단/허용 단독 결정).

---

## 🛠 시놀로지 배포 함정 (2026-04-27 사고로 발견)

코드를 시놀로지에 새로 배포하거나 인증 트러블슈팅 시 반드시 확인:

1. **`accts-runtime` / `gmini-accts-runtime` 폴더 권한** — 호스트에서 `chown -R 1000:1000 /volume1/FlowDesk/{accts-runtime,gmini-accts-runtime}` + `chmod 700`. Windows SMB로 폴더 만들면 uid 1000이 못 써서 `Permission denied` → OAuth 계정 sync 실패 → 라운드로빈 시 "Not logged in".
2. **재시작 ≠ 재빌드** — `server.py`/`Dockerfile` 수정 후엔 Container Manager에서 단순 "재시작"이 아닌 **빌드(Build/Rebuild)** 메뉴 선택해야 코드 적용. 단순 재시작은 옛 이미지 그대로.
3. **DB의 stale OAuth 계정 정리** — 옛 `.credentials.json` 업로드본이 라운드로빈 우선순위로 먼저 시도되어 401 발생. setup-token 활성화 후 다른 모든 OAuth 계정 삭제 권장.
4. **부팅 로그 sanity check**:
   - ✅ `Active Claude account is setup-token — env var mode (no global file)` (active이 setup-token)
   - ✅ `Synced N Claude accounts (preserve-existing)` (새 코드 표식)
   - ❌ `Sync accounts failed: Permission denied` (폴더 chown 필요)
   - ❌ `Sync accounts ... to temp dirs` (옛 코드 돌고 있음, 빌드 안 됨)

---

## 🚫 하지 말 것

1. **불필요한 모듈/프레임워크 도입** (React, Webpack, Vite 등) — 단일 파일 구조 유지.
2. **사용자에게 `.credentials.json` 업로드 안내** — 항상 setup-token.
3. **`docker-compose.yml`의 bind mount 제거** — `accts-runtime` / `gmini-accts-runtime` 마운트가 빠지면 인증 실패 사이클 부활.
4. **저장 모달에 워크스페이스 경로 입력창 추가** — 정책 고정 위반.
5. **이미지를 단순 "재시작"만으로 코드 변경 반영 시도** — 반드시 빌드(rebuild)해야 적용됨.

---

## 📚 더 깊은 컨텍스트

이 파일은 의도적으로 짧게 유지합니다. 더 넓은 배경(노드 타입 카탈로그, API 엔드포인트, 단축키 등)은 `README.md` 참고. 사용자 가이드/배포는 `DEPLOYMENT.md`, 트러블슈팅은 `TROUBLESHOOTING.md` 참고.
