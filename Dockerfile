# syntax=docker/dockerfile:1
# localdub in one container: the web app (the Rust server and the page) and the engine (Python with
# ffmpeg, every language's spelling dictionary, yt-dlp). No GPU in here: the models stay on your GPU
# box (llama-swap) or any OpenAI-compatible server, set with LOCALDUB_LLM_URL or the Models screen.
#   docker compose up -d        (compose.yaml: the model server, the data volume, the port)
# Build argument SPEAKER=0 leaves out the speaker model (WavLM on the CPU, about 1 GB of PyTorch),
# which picks voice samples and takes by how much they sound like the speaker.

# --- the page --------------------------------------------------------------------------------------
FROM node:24-trixie-slim AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund --loglevel=error
COPY web/ ./
RUN npm run build

# --- the server ------------------------------------------------------------------------------------
FROM rust:1.99.1-slim-trixie AS server
WORKDIR /src/server
COPY server/ ./
RUN cargo build --release --locked && cp target/release/localdub-server /localdub-server

# --- the engine, and everything together -------------------------------------------------------------
FROM debian:trixie-slim
ARG SPEAKER=1
LABEL org.opencontainers.image.title="localdub" \
      org.opencontainers.image.description="Dub videos into another language in the creator's own cloned voice, and cut Shorts" \
      org.opencontainers.image.source="https://github.com/ivanwe2/localdub" \
      org.opencontainers.image.licenses="MIT"

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl unzip ffmpeg hunspell fontconfig fonts-dejavu-core python3 python3-venv \
 && rm -rf /var/lib/apt/lists/*

ENV VIRTUAL_ENV=/opt/localdub \
    PATH=/opt/localdub/bin:$PATH \
    PIP_DISABLE_PIP_VERSION_CHECK=1
COPY pyproject.toml README.md /src/engine/
COPY src/ /src/engine/src/
RUN python3 -m venv /opt/localdub \
 && pip install --no-cache-dir /src/engine \
 && if [ "$SPEAKER" = 1 ]; then \
      pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch \
      && pip install --no-cache-dir "/src/engine[voice]"; \
    fi \
 && rm -rf /src/engine

# every language's spelling dictionary: the Debian packages the engine's language table names,
# one at a time, so one missing from this Debian release doesn't stop the rest
RUN apt-get update \
 && for package in $(localdub languages --apt all); do \
      apt-get install -y --no-install-recommends "$package" || echo "no package $package here"; \
    done \
 && rm -rf /var/lib/apt/lists/*

# yt-dlp and Deno (which yt-dlp needs for YouTube), checked against their published checksums;
# a newer yt-dlp comes with a newer image (CI rebuilds it every week)
COPY deploy/server/yt-dlp.sh /tmp/yt-dlp.sh
RUN bash /tmp/yt-dlp.sh && rm /tmp/yt-dlp.sh

COPY --from=server /localdub-server /usr/local/bin/localdub-server
COPY --from=web /src/web/dist /app/web
COPY --chmod=755 deploy/docker/entrypoint.sh /usr/local/bin/localdub-entrypoint

RUN useradd --create-home --uid 1000 localdub && mkdir /data && chown localdub: /data
USER localdub
ENV LOCALDUB_HOME=/data \
    LOCALDUB_LISTEN=0.0.0.0:8080 \
    LOCALDUB_WEB=/app/web \
    LOCALDUB_ENGINE=localdub \
    LOCALDUB_YTDLP=/usr/local/bin/yt-dlp \
    HF_HOME=/data/.cache/huggingface \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD curl -fsS http://127.0.0.1:8080/api/health || exit 1
ENTRYPOINT ["localdub-entrypoint"]
CMD ["localdub-server"]
