# Gil's FlowDesk — Docker image
FROM python:3.12-slim

# Node.js 22 + 시스템 도구
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get install -y --no-install-recommends lsof \
    && rm -rf /var/lib/apt/lists/*

# Python deps
RUN pip install --no-cache-dir psycopg2-binary

# Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# 일반 사용자 생성 (Claude CLI가 root에서 --dangerously-skip-permissions 거부함)
RUN useradd -m -u 1000 -s /bin/bash flowdesk

# App 디렉토리 (flowdesk 소유)
WORKDIR /app
COPY --chown=flowdesk:flowdesk server.py canvas.html chat.html /app/
COPY --chown=flowdesk:flowdesk migrate_to_pg.py setup_pg.sql /app/

# 볼륨 마운트 포인트 (bind mount로 덮어써짐)
RUN mkdir -p /workspace /claude-creds /app/uploads \
    && chown -R flowdesk:flowdesk /workspace /claude-creds /app/uploads \
    && ln -sf /claude-creds /home/flowdesk/.claude

# 비-root 사용자로 전환
USER flowdesk
ENV HOME=/home/flowdesk

EXPOSE 8888
ENV PYTHONUNBUFFERED=1

CMD ["python3", "server.py"]
