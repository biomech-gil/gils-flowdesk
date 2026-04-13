# Gil's FlowDesk — 시놀로지 배포 트러블슈팅 가이드

> **⚠️ 이 문서는 실제로 헤맸던 문제들의 기록임. 비슷한 증상 나오면 여기서 먼저 확인할 것.**

---

## 🚨 배포 전 반드시 알아야 할 것 (TOP 5)

### 1. **컨테이너는 root가 아닌 `flowdesk` (uid 1000) 사용자로 동작함**
- 이유: Claude CLI가 root에서 `--dangerously-skip-permissions` 거부 (보안 정책)
- 결과: bind mount된 시놀로지 폴더도 **uid 1000 소유여야 함**
- **잊으면**: Permission denied 에러 폭주

### 2. **시놀로지 폴더 권한 사전 설정 필수**
- `/volume1/FlowDesk/{workspace,creds,uploads}` 는 **uid 1000 소유**
- `/volume1/FlowDesk/db` 는 postgres가 알아서 (건드리지 말 것)
- 자동화: `setup-synology.sh` 한 번 실행 (sudo 필요)

### 3. **`server.py` 수정은 단순 재시작으로 반영 안 됨 — 재빌드 필수**
- Dockerfile에서 `COPY` 로 이미지에 굽혀짐
- 호스트 파일 바꿔도 컨테이너 안엔 옛날 버전
- 재빌드 명령:
  ```bash
  sudo docker-compose up -d --build flowdesk
  ```

### 4. **DB 비밀번호는 한 번 정하면 못 바꿈 (데이터 살리려면)**
- PostgreSQL 컨테이너가 첫 실행 때 `.env`의 `DB_PASSWORD`로 데이터 초기화
- 그 다음 비번 바꾸면 → 인증 실패로 컨테이너 시작 안 됨
- 해결: 원래 비번으로 되돌리거나, `/volume1/FlowDesk/db/` 비우고 처음부터

### 5. **외부 접속 = 공유기 포트포워딩 + 호스트 파일 권한 둘 다 필요**
- 포트포워딩: 9090 (또는 `.env`의 PORT) → 시놀로지 IP
- 시놀로지 방화벽: 9090 허용

---

## 🔧 자주 발생하는 에러와 해결

### ❌ `--dangerously-skip-permissions cannot be used with root/sudo`

**원인**: Dockerfile에서 `USER flowdesk` 디렉티브 누락 (또는 컨테이너가 root로 실행됨)

**해결**: Dockerfile 확인:
```dockerfile
RUN useradd -m -u 1000 -s /bin/bash flowdesk
USER flowdesk
ENV HOME=/home/flowdesk
```
없으면 추가하고 재빌드.

---

### ❌ `Permission denied: '/app/config.json'`

**원인**: `/app` 디렉토리 자체가 root 소유. `WORKDIR /app`은 폴더를 root 소유로 만들고, `COPY --chown` 은 **파일만** 바꿈.

**해결**: Dockerfile에 `/app` 도 chown 대상에 포함:
```dockerfile
RUN mkdir -p /workspace /claude-creds /app/uploads \
    && chown -R flowdesk:flowdesk /app /workspace /claude-creds \
    && ln -sf /claude-creds /home/flowdesk/.claude
```

---

### ❌ `Permission denied: '/workspace/...'` 또는 `'/claude-creds/...'`

**원인**: 시놀로지 호스트 폴더가 root 소유라 컨테이너의 flowdesk(uid 1000)가 못 씀

**해결** (SSH로):
```bash
sudo chown -R 1000:1000 /volume1/FlowDesk/workspace /volume1/FlowDesk/creds /volume1/FlowDesk/uploads
```

또는 `bash setup-synology.sh` 실행.

---

### ❌ `Key (project_id)=() is not present in table "projects"` (FK 위반)

**원인**: 캔버스에서 프로젝트 미선택 상태로 노드 실행. 빈 문자열이 PostgreSQL FK 제약 위반.

**해결**: `server.py` 에서 빈 문자열을 NULL로 변환:
```python
db_exec("INSERT INTO executions ...",
        (exec_id, project_id or None, node_id, ...))
```

---

### ❌ `password authentication failed for user "canvas"` (DB 인증 실패)

**원인**: `.env`의 `DB_PASSWORD` 와 기존 DB 데이터의 비번 불일치

**해결 A** (데이터 보존): `.env`의 비번을 원래 값으로 되돌리기  
**해결 B** (데이터 포기): `/volume1/FlowDesk/db/` 비우고 컨테이너 재시작
```bash
sudo docker-compose down
sudo rm -rf /volume1/FlowDesk/db/*
sudo docker-compose up -d
```

---

### ❌ CORS 에러 / 로그인 안 됨 (브라우저 콘솔에 "blocked by CORS policy")

**원인**: HTML에 API 주소가 `http://...:8888` 처럼 하드코딩됨. 9090 포트나 리버스 프록시(HTTPS) 통하면 깨짐.

**해결**: `canvas.html`, `chat.html` 에서:
```javascript
const API = location.origin;  // ✅
// const API = 'http://시놀로지IP:8888';  // ❌
```

---

### ❌ `scp: subsystem request failed on channel 0` (시놀로지 SCP 실패)

**원인**: 시놀로지가 SFTP 서브시스템을 끄거나 신형 scp 프로토콜 미지원

**해결**: `-O` 플래그로 legacy 모드 강제:
```powershell
scp -O -P 5006 D:\file.py user@host:/path/file.py
```

또는 File Station GUI로 업로드.

---

### ❌ ssh 명령어가 무반응 / `sudo: a terminal is required`

**원인**: `ssh` 에 `-t` 플래그 없어서 sudo 비번 입력용 TTY 할당 안 됨

**해결**:
```powershell
ssh -p 5006 -t user@host "sudo ..."
```
(`-t` 추가)

---

### ❌ `.env` 파일이 File Station에 안 보임

**원인**: 점(.)으로 시작하는 파일은 시놀로지 File Station에서 기본 숨김

**해결**: File Station 우상단 **설정** → **숨겨진 파일 표시** 체크

---

### ❌ 노드 실행 시 `5회 재시도 실패`

**진짜 원인은 컨테이너 로그에 있음.** 위의 "Permission denied" 또는 FK 위반인 경우가 대부분.

**확인**:
```powershell
ssh -p 5006 -t user@host "sudo docker logs --tail=100 flowdesk-app"
```

또는 Container Manager → 컨테이너 → flowdesk-app → **로그** 탭

---

### ❌ 컨테이너가 계속 재시작 (Restarting...)

원인 후보:
1. DB 비번 불일치 → 위 "DB 인증 실패" 참고
2. `/app/config.json` 권한 문제 → 위 "Permission denied" 참고
3. Python 코드 에러 → 로그에서 traceback 확인
4. postgres 컨테이너가 아직 안 떴음 (보통 자동 해결, 30초 대기)

**디버그 기본값**:
```powershell
ssh -p 5006 -t user@host "sudo docker logs --tail=200 flowdesk-app"
```

---

## 🔐 보안 사고 방지 (실수 경험담)

### ⚠️ SSH 세션에 비밀번호 잘못 입력하면 화면에 노출됨
- SSH 비번 프롬프트 (`password:`) 에서 입력은 안 보임 (정상)
- 근데 **로그인 후 셸에 들어와서** 비번을 명령처럼 치면 그대로 보임
- → 셸 히스토리(`~/.bash_history`)에 남음
- 발생했으면: `history -c && history -w` 로 지우고, 비번 변경

### ⚠️ SSH 비밀번호/접속정보를 절대 외부에 공유하지 말 것
- 한 번 노출되면 NAS 전체가 위험
- 명령어만 받아서 본인이 직접 실행

---

## 📋 배포 체크리스트 (새로 셋업할 때)

1. [ ] 시놀로지에 폴더 생성: `/volume1/FlowDesk/{db,workspace,creds,uploads}`
2. [ ] 폴더 권한: `sudo chown -R 1000:1000 /volume1/FlowDesk/{workspace,creds,uploads}`
3. [ ] 앱 파일을 `/volume1/docker/gils-flowdesk/` 에 배치
4. [ ] `.env` 파일 작성 (DB_PASSWORD, PORT 등)
5. [ ] Container Manager에서 프로젝트 생성 + 빌드
6. [ ] 빌드 완료 확인 (5~10분)
7. [ ] 컨테이너 로그에 PermissionError 없는지 확인
8. [ ] 브라우저 접속 → 로그인 → ⚙️ 설정에서 Claude credentials.json 업로드
9. [ ] 노드 만들어서 실행 테스트
10. [ ] 외부 접속 (공유기 포트포워딩 + 방화벽)

---

## 🆘 응급 복구 명령 모음

```bash
# 로그 100줄 보기
sudo docker logs --tail=100 flowdesk-app

# 로그 실시간 추적
sudo docker logs -f flowdesk-app

# 컨테이너 재시작 (이미지는 그대로)
sudo docker-compose restart flowdesk

# 이미지 재빌드 후 재시작 (server.py 수정 시 필수)
sudo docker-compose up -d --build flowdesk

# 모든 컨테이너 중지
sudo docker-compose down

# 모든 컨테이너 시작
sudo docker-compose up -d

# DB 초기화 (⚠️ 데이터 다 사라짐)
sudo docker-compose down
sudo rm -rf /volume1/FlowDesk/db/*
sudo docker-compose up -d

# 컨테이너 안에 들어가서 직접 확인
sudo docker exec -it flowdesk-app bash

# 폴더 권한 일괄 수정
sudo chown -R 1000:1000 /volume1/FlowDesk/workspace /volume1/FlowDesk/creds /volume1/FlowDesk/uploads

# 권한 상태 확인
ls -la /volume1/FlowDesk/
```

---

## 🌐 외부 접속 SSH 템플릿

```powershell
# 기본 접속
ssh -p 5006 user@host

# 명령 한 줄 실행 (sudo 포함시 -t 필수)
ssh -p 5006 -t user@host "sudo docker logs --tail=50 flowdesk-app"

# 파일 업로드 (시놀로지는 -O 필수)
scp -O -P 5006 로컬파일 user@host:/원격경로

# 파일 다운로드
scp -O -P 5006 user@host:/원격파일 로컬경로
```

---

## 📝 변경 이력으로 본 주요 함정

| 커밋 | 함정 | 교훈 |
|---|---|---|
| `fix: API uses location.origin` | 하드코딩된 `:8888` 때문에 9090 포트에서 CORS | URL은 `location.origin` 으로 |
| `fix: run as non-root flowdesk` | Claude CLI가 root 거부 | `USER flowdesk` 필수 |
| `feat: setup-synology.sh` | bind mount 폴더 권한 | uid 1000 매칭 |
| `fix: chown /app` | `WORKDIR` 가 root 소유로 만듦 | 디렉토리 자체도 chown |
| `fix: project_id NULL` | 빈 문자열 ≠ NULL (PG에선) | `value or None` 패턴 |

---

**이 문서를 자주 업데이트하세요. 새 함정 발견하면 바로 추가.**
