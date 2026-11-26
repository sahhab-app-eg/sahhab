FROM python:3.11-slim

# Node.js 22 (needed by the bgutil PO-token provider server)
COPY --from=node:22-slim /usr/local /usr/local

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates curl unzip ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Deno (JS runtime for yt-dlp's YouTube n-challenge solver)
RUN curl -fsSL -o /tmp/deno.zip https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip \
    && unzip -o /tmp/deno.zip -d /usr/local/bin \
    && rm /tmp/deno.zip && deno --version

RUN pip install --no-cache-dir "yt-dlp==2026.10.21.202512.dev0" bgutil-ytdlp-pot-provider==2.0.2

RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user SAHHAB_HOST=0.0.0.0 PORT=7860 SAHHAB_DIR=/home/user/Sahhab PYTHONUNBUFFERED=1

# bgutil POT provider: HTTP server on 127.0.0.1:4416, built at image build time
RUN git clone -q --depth 1 --branch 2.0.2 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /home/user/bgutil \
    && cd /home/user/bgutil/server \
    && npm ci --no-fund --no-audit \
    && npx tsc \
    && npm prune --omit=dev

WORKDIR /home/user/app
COPY --chown=user app.py start.sh yt_chain.py ./
COPY --chown=user vendor ./vendor
EXPOSE 7860
CMD ["sh", "start.sh"]
