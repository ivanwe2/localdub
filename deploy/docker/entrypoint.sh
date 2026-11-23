#!/bin/sh
# The container's entrypoint: the data folder, the GPU panel pointed at the model server, then the
# command (localdub-server, or e.g. `localdub check` with `docker compose run localdub localdub check`).
set -eu
mkdir -p "$LOCALDUB_HOME/videos"
# the status panel (models loaded, GPUs) asks llama-swap; by default the same one the engine uses
if [ -z "${LOCALDUB_MODELS_URL:-}" ]; then
  LOCALDUB_MODELS_URL=${LOCALDUB_LLM_URL:-none}
  export LOCALDUB_MODELS_URL
fi
exec "$@"
