# Development image for localdub: everything needed to build and test the engine (Python), the server
# (Rust) and the web UI (Node) on Linux, the same OS family as CI and the GPU box (Debian trixie).
# Rust and Node are added by the devcontainer features in devcontainer.json; this file adds the
# system tools and an empty Python venv.
FROM debian:trixie-slim

# One hunspell dictionary package per language the channels speak; override at build time,
# e.g. HUNSPELL_DICTS="hunspell-bg hunspell-en-us hunspell-de-de".
ARG HUNSPELL_DICTS="hunspell-bg hunspell-en-us"

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git sudo build-essential pkg-config \
      python3 python3-venv \
      ffmpeg hunspell ${HUNSPELL_DICTS} fontconfig fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

# A non-root user, so files created in the mounted repo aren't root's. On Linux hosts the dev container
# tooling changes this user's uid to match yours and re-owns the HOME folder only, so everything the
# user must write lives under it: ~/.venv (the engine's Python packages) and ~/.cargo-target (Rust build
# output, a Docker volume, see devcontainer.json; building on a folder mounted from Windows is slow).
ARG USERNAME=dev
RUN useradd --create-home --uid 1000 --shell /bin/bash ${USERNAME} \
 && echo "${USERNAME} ALL=(root) NOPASSWD:ALL" > /etc/sudoers.d/${USERNAME} \
 && python3 -m venv /home/${USERNAME}/.venv \
 && mkdir /home/${USERNAME}/.cargo-target \
 && chown -R ${USERNAME}: /home/${USERNAME}

ENV PATH=/home/${USERNAME}/.venv/bin:$PATH \
    CARGO_TARGET_DIR=/home/${USERNAME}/.cargo-target
