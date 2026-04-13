# Gil's FlowDesk — Docker image
FROM python:3.12-slim

# Node.js 22 for Claude Code CLI
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get install -y --no-install-recommends \
    lsof \
    && rm -rf /var/lib/apt/lists/*

# Python deps
RUN pip install --no-cache-dir psycopg2-binary

# Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# App
WORKDIR /app
COPY server.py canvas.html chat.html /app/
COPY migrate_to_pg.py setup_pg.sql /app/

# Volumes for persistent data
RUN mkdir -p /workspace /claude-creds /app/uploads \
    && ln -s /claude-creds /root/.claude

EXPOSE 8888
ENV PYTHONUNBUFFERED=1

CMD ["python3", "server.py"]
