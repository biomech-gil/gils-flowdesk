# Gil's FlowDesk — 배포 가이드 (처음 설치하는 사람용)

이 문서는 Docker/시놀로지 경험이 없는 분도 따라할 수 있도록 작성되었습니다.

---

## 📋 이 시스템이 뭐하는 건가

- **웹 기반 AI 워크플로우 에디터** (브라우저로 접속해서 사용)
- **노드**(박스)를 캔버스에 배치하고 선으로 연결해서 Claude AI에게 단계별 작업 지시
- **메모장, 프로젝트 관리, 이미지 첨부** 통합
- 시놀로지 NAS에 올려두면 **어디서든 접속 가능**한 개인 작업 공간

---

## 🏗️ 전체 구조 이해

```
[웹 브라우저 (내 노트북/휴대폰)]
          ↓ HTTPS/HTTP
[공유기/포트포워딩]
          ↓
[시놀로지 NAS]
   └─ Docker:
       ├─ flowdesk-app (웹 서버) ← 8888 포트
       └─ flowdesk-db  (PostgreSQL) ← 내부 전용
           ↑
       /volume1/FlowDesk/  ← 데이터 실제 저장 위치
         ├─ db/           (DB 파일)
         ├─ workspace/    (작업 폴더)
         ├─ creds/        (Claude 인증)
         └─ uploads/      (이미지)
```

핵심:
- **앱(flowdesk-app)** 과 **DB(postgres)** 가 **2개의 컨테이너**로 나뉘어 있음
- 컨테이너는 지우거나 재생성해도 괜찮음 (데이터는 `/volume1/FlowDesk/`에 남아있음)
- `docker-compose.yml` 파일 하나로 둘을 엮어서 관리

---

## 🎯 배포 전 결정 사항 (중요!)

### 1. 어디에서 접속할 건가?

| 시나리오 | 필요한 것 |
|---------|----------|
| **집에서만** | 시놀로지 내부 IP로 접속 (`192.168.x.x:8888`) |
| **외부에서도 (핸드폰 4G 등)** | 공유기 포트포워딩 + DDNS (또는 시놀로지 QuickConnect) |
| **HTTPS 보안** | 시놀로지 DSM의 **리버스 프록시** 설정 |

### 2. 포트는 뭘로 할 건가?

시놀로지가 이미 쓰는 포트를 피해야 합니다.

**피해야 할 포트**:
- `5000, 5001` — DSM 관리
- `80, 443` — Web Station (웹 스테이션 사용 중이면)
- `22` — SSH

**안전한 포트 예**: `8888`, `9090`, `18080`, `28888`

`.env`에서 `PORT=원하는숫자` 로 설정.

### 3. DB 데이터는 어디에 저장할 건가?

**옵션 A (추천)**: 시놀로지 파일 시스템에 직접 저장
```env
DB_DATA_HOST=/volume1/FlowDesk/db
```
- 장점: 파일 탐색기/FTP로 직접 접근, 백업 쉬움, 다른 서비스와 공유 가능
- 단점: 경로 실수하면 복잡해짐

**옵션 B**: Docker volume (기본값)
- `DB_DATA_HOST` 를 설정 안 하면 자동으로 Docker 내부 volume 사용
- 장점: 간단함
- 단점: 컨테이너 지우면 같이 사라질 위험, 직접 접근 어려움

### 4. 데이터 누적 관리

데이터가 계속 쌓이면:
- **이미지 업로드** → `/volume1/FlowDesk/uploads/` 에 쌓임 (주기적 정리 필요)
- **임시저장(temps)** → 30일 자동 정리됨
- **실행 이력(executions)** → 계속 누적 (수동 정리 필요할 수 있음)
- **DB 파일** → 전체 데이터 저장, 가장 중요

**해결**:
- DB가 커지면 → SSD 볼륨에 `DB_DATA_HOST` 설정
- uploads 폴더 주기적 확인/정리

---

## 🚀 시놀로지 배포 단계별 가이드

### STEP 1 — 시놀로지 준비

1. **Container Manager 설치** (DSM 패키지 센터에서)
2. **SSH 활성화** (제어판 → 단말기 및 SNMP → SSH 체크)
3. **폴더 구조 생성** (File Station 또는 SSH로):
   ```
   /volume1/docker/gils-flowdesk/   ← 앱 파일 배치할 곳
   /volume1/FlowDesk/db/            ← DB 저장소
   /volume1/FlowDesk/workspace/     ← 작업 폴더
   /volume1/FlowDesk/creds/         ← Claude 인증
   /volume1/FlowDesk/uploads/       ← 업로드 파일
   ```

### STEP 2 — 앱 파일 업로드

방법 A (SSH + git):
```bash
cd /volume1/docker
git clone https://github.com/biomech-gil/gils-flowdesk.git
cd gils-flowdesk
```

방법 B (File Station으로 업로드):
- GitHub에서 ZIP 다운로드
- `/volume1/docker/gils-flowdesk/` 에 압축 해제

### STEP 3 — `.env` 파일 작성

```bash
cd /volume1/docker/gils-flowdesk
cp .env.example .env
vi .env   # 또는 File Station의 텍스트 편집기로
```

수정할 항목:
```env
DB_PASSWORD=my_strong_password_2026!     # 반드시 변경
PORT=8888                                # 필요시 변경
DB_DATA_HOST=/volume1/FlowDesk/db
WORKSPACE_HOST=/volume1/FlowDesk/workspace
CLAUDE_CREDS_HOST=/volume1/FlowDesk/creds
UPLOADS_HOST=/volume1/FlowDesk/uploads
```

### STEP 4 — Container Manager로 프로젝트 생성

1. **Container Manager 열기**
2. **프로젝트** 탭 → **생성**
3. 설정:
   - 프로젝트 이름: `gils-flowdesk`
   - 경로: `/volume1/docker/gils-flowdesk`
   - 소스: **docker-compose.yml 업로드** 또는 **기존 docker-compose.yml 사용** 선택
4. **다음** → **완료**

처음 빌드는 5~10분 걸립니다 (Node.js, Claude CLI 설치).

### STEP 5 — 첫 로그인

1. 브라우저: `http://시놀로지IP:8888`
2. 로그인:
   - 아이디: `gilhojong`
   - 비밀번호: `!!Il197119!!`
3. **⚙️ 설정** 버튼 클릭
4. Claude 인증 업로드:
   - 본인 PC에서 `claude login` 한 번 실행
   - `~/.claude/.credentials.json` (또는 `C:\Users\사용자\.claude\.credentials.json`) 파일 복사
   - 웹에서 **📂 파일에서** → 그 파일 선택 → **💾 저장 + 적용**
5. 작업 폴더 루트 설정: `/workspace` (컨테이너 내부 경로)

### STEP 6 — 외부 접속 (전국에서 쓰려면)

**옵션 A — 공유기 포트포워딩**:
1. 공유기 관리 페이지 접속 (보통 `192.168.0.1` 또는 `192.168.1.1`)
2. 포트포워딩 설정:
   - 외부 포트: `8888` (또는 원하는 숫자)
   - 내부 IP: 시놀로지 IP
   - 내부 포트: `.env`의 PORT와 동일
3. DDNS 설정 (시놀로지 DSM → 제어판 → 외부 접근 → DDNS)

**옵션 B — 시놀로지 QuickConnect**:
- 더 간단하지만 속도 느림
- DSM → 제어판 → 외부 접근 → QuickConnect

**옵션 C — HTTPS 리버스 프록시** (권장):
- DSM → 제어판 → 로그인 포털 → 고급 → 리버스 프록시
- 소스: `https://flowdesk.mynas.com`
- 대상: `http://localhost:8888`
- 이러면 `https://flowdesk.mynas.com` 으로 안전하게 접속 가능

---

## ⚠️ 자주 하는 실수 / 주의사항

### 1. 포트 충돌
- 에러: "port is already allocated"
- 해결: `.env` 의 `PORT` 를 다른 숫자로 변경 후 `docker-compose up -d`

### 2. DB 비밀번호 변경 후 재시작 안 됨
- 원인: 기존 DB 데이터의 비밀번호와 불일치
- 해결: 
  - 기존 DB 유지하려면 비밀번호 다시 원래대로
  - 새로 시작해도 되면 `/volume1/FlowDesk/db/` 내용 삭제 후 재실행

### 3. Claude 인증 만료
- 증상: 노드 실행 시 "authentication failed"
- 해결: 본인 PC에서 `claude login` → 새 `.credentials.json` 업로드

### 4. 업로드 폴더 용량 폭증
- 주기적으로 `/volume1/FlowDesk/uploads/` 확인
- 오래된 이미지는 수동 삭제

### 5. 컨테이너가 자꾸 재시작
- 로그 확인: Container Manager → 해당 컨테이너 → **로그** 탭
- 흔한 원인:
  - DB 연결 실패 → postgres 컨테이너가 먼저 준비 안 됨 (잠시 기다리면 자동 해결)
  - 포트 충돌
  - 볼륨 경로 권한 문제

### 6. 데이터 백업
- `/volume1/FlowDesk/` 전체를 주기적으로 백업 (Hyper Backup 권장)
- DB만 백업하려면:
  ```bash
  docker exec flowdesk-db pg_dump -U canvas canvas_db > backup.sql
  ```

---

## 🔄 업데이트 방법

```bash
cd /volume1/docker/gils-flowdesk
git pull
docker-compose up -d --build
```

또는 Container Manager → 프로젝트 → **재빌드**

---

## 🆘 완전 초기화 (데이터 다 지우고 새로)

⚠️ **모든 데이터 사라짐. 신중히!**

```bash
cd /volume1/docker/gils-flowdesk
docker-compose down
rm -rf /volume1/FlowDesk/db/*
rm -rf /volume1/FlowDesk/uploads/*
docker-compose up -d
```

---

## 📞 문제 생기면

1. Container Manager → 컨테이너 → **로그** 탭 확인
2. 시놀로지 SSH:
   ```bash
   cd /volume1/docker/gils-flowdesk
   docker-compose logs --tail=50
   ```
3. 서버 재시작:
   ```bash
   docker-compose restart
   ```

---

## 🔐 보안 권장사항

- **DB_PASSWORD** 는 반드시 강력하게 (16자+ 대소문자+숫자+특수문자)
- **로그인 비밀번호** (`gilhojong` 계정)도 변경 권장 (DB에서 직접 수정 필요)
- **HTTPS** 사용 (시놀로지 리버스 프록시)
- 포트포워딩 시 포트를 **비표준**으로 (8888보다 28888 등 랜덤)
- 정기적으로 시놀로지 DSM 업데이트
