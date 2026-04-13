#!/bin/bash
# Gil's FlowDesk — Synology 폴더 초기 설정 스크립트
# SSH로 시놀로지 접속 후 sudo 권한으로 1회만 실행하면 됨
#
# 사용법:
#   sudo bash setup-synology.sh
#
# 하는 일:
#   1. /volume1/FlowDesk/{db,workspace,creds,uploads} 폴더 생성
#   2. workspace/creds/uploads 를 uid 1000 (컨테이너의 flowdesk 사용자) 소유로 변경
#   3. db 폴더는 postgres 컨테이너가 알아서 처리하므로 그대로 둠

set -e

BASE=/volume1/FlowDesk

echo "==> 폴더 생성: $BASE/{db,workspace,creds,uploads}"
mkdir -p "$BASE/db" "$BASE/workspace" "$BASE/creds" "$BASE/uploads"

echo "==> 권한 설정 (uid 1000 = 컨테이너 내부 flowdesk 사용자)"
chown -R 1000:1000 "$BASE/workspace" "$BASE/creds" "$BASE/uploads"
chmod -R u+rwX "$BASE/workspace" "$BASE/creds" "$BASE/uploads"

echo "==> db 폴더는 postgres 이미지가 알아서 처리 (건드리지 않음)"

echo ""
echo "✅ 완료. 이제 다음을 진행하세요:"
echo "   1. /volume1/docker/gils-flowdesk/ 에 앱 파일 배치"
echo "   2. .env 파일 작성"
echo "   3. Container Manager → 프로젝트 → 빌드"
echo ""
ls -la "$BASE"
