# Gil's FlowDesk — Docker image
FROM python:3.12-slim

# Node.js 22 + 시스템 도구 (ffmpeg는 아래에서 정적 바이너리로 빠르게 설치)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates gnupg xz-utils \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs lsof \
    && rm -rf /var/lib/apt/lists/*

# ffmpeg 정적 바이너리 (johnvansickle 공식 정적 빌드, 의존성 없음 · apt ffmpeg 대비 10배+ 빠름)
RUN curl -fsSL https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz -o /tmp/ffmpeg.tar.xz \
    && mkdir -p /tmp/ffmpeg \
    && tar -xJf /tmp/ffmpeg.tar.xz -C /tmp/ffmpeg --strip-components=1 \
    && mv /tmp/ffmpeg/ffmpeg /tmp/ffmpeg/ffprobe /usr/local/bin/ \
    && chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe \
    && rm -rf /tmp/ffmpeg /tmp/ffmpeg.tar.xz

# Python deps + yt-dlp (다운로더) + openpyxl (Sheet xlsx I/O) + YouTube 검색/자막
RUN pip install --no-cache-dir \
    psycopg2-binary \
    yt-dlp \
    openpyxl \
    google-api-python-client \
    youtube-transcript-api \
    faster-whisper

# Whisper 모델 캐시 디렉토리 (첫 실행 시 ~470MB small 모델 자동 다운)
RUN mkdir -p /app/whisper-cache && chown -R 1000:1000 /app/whisper-cache
ENV WHISPER_CACHE_DIR=/app/whisper-cache

# Claude Code CLI + Gemini CLI
RUN npm install -g @anthropic-ai/claude-code @google/gemini-cli

# 일반 사용자 생성 (Claude CLI가 root에서 --dangerously-skip-permissions 거부함)
RUN useradd -m -u 1000 -s /bin/bash flowdesk

# App 디렉토리 (flowdesk 소유)
WORKDIR /app
COPY --chown=flowdesk:flowdesk server.py canvas.html chat.html /app/
COPY --chown=flowdesk:flowdesk migrate_to_pg.py setup_pg.sql /app/

# 볼륨 마운트 포인트 (bind mount로 덮어써짐)
# /app 자체도 chown 해야 server.py가 config.json 등 새 파일 생성 가능
RUN mkdir -p /workspace /claude-creds /gemini-creds /app/uploads \
    && chown -R flowdesk:flowdesk /app /workspace /claude-creds /gemini-creds \
    && ln -sf /claude-creds /home/flowdesk/.claude \
    && ln -sf /gemini-creds /home/flowdesk/.gemini

# 비-root 사용자로 전환
USER flowdesk
ENV HOME=/home/flowdesk

EXPOSE 8888
ENV PYTHONUNBUFFERED=1

CMD ["python3", "server.py"]
