#!/bin/bash
# Gil's FlowDesk — Synology 폴더 초기 설정 스크립트
# SSH로 시놀로지 접속 후 sudo 권한으로 1회만 실행하면 됨
# 재실행해도 안전 (mkdir -p / chown 전부 idempotent)
#
# 사용법:
#   sudo bash setup-synology.sh
#
# 하는 일:
#   1. /volume1/FlowDesk/{db,workspace,creds,gemini-creds,uploads,whisper-cache,
#                         accts-runtime,gmini-accts-runtime} 폴더 생성
#   2. 컨테이너가 쓸 폴더는 uid 1000 (flowdesk 사용자) 소유로 변경
#   3. db 폴더는 postgres 이미지가 알아서 처리하므로 소유권 안 바꿈
#
# ⚠️ accts-runtime / gmini-accts-runtime 은 멀티계정 CLI 토큰 갱신본 영구 보존용 폴더.
#    Windows SMB 등으로 만들면 uid 1000 권한이 안 붙어 인증 실패가 반복됨.
#    이 스크립트로 SSH에서 만들어야 안전.

set -e

BASE=/volume1/FlowDesk
SUB_DIRS=(db workspace creds gemini-creds uploads whisper-cache accts-runtime gmini-accts-runtime)
CHOWN_DIRS=(workspace creds gemini-creds uploads whisper-cache accts-runtime gmini-accts-runtime)
CHMOD_700_DIRS=(accts-runtime gmini-accts-runtime)

echo "==> 폴더 생성: $BASE/{$(IFS=,;echo "${SUB_DIRS[*]}")}"
for d in "${SUB_DIRS[@]}"; do
  mkdir -p "$BASE/$d"
done

echo "==> 권한 설정 (uid 1000 = 컨테이너 내부 flowdesk 사용자)"
for d in "${CHOWN_DIRS[@]}"; do
  chown -R 1000:1000 "$BASE/$d"
  chmod -R u+rwX "$BASE/$d"
done

# 인증 토큰 폴더는 더 엄격하게 (700 — 소유자만 읽기·쓰기·실행)
for d in "${CHMOD_700_DIRS[@]}"; do
  chmod 700 "$BASE/$d"
done

echo "==> db 폴더는 postgres 이미지가 알아서 처리 (건드리지 않음)"

echo ""
echo "✅ 완료. 이제 다음을 진행하세요:"
echo "   1. /volume1/docker/gils-flowdesk/ 에 앱 파일 배치"
echo "   2. .env 파일 작성 (YOUTUBE_API_KEY, DB_PASSWORD 등)"
echo "   3. Container Manager → 프로젝트 → 빌드"
echo ""
ls -la "$BASE"
