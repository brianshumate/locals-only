#!/usr/bin/env bash
# Verify every external dependency of the eval pipeline. Exits non-zero if
# anything required is missing. Backend-aware: checks LM Studio on macOS and
# llama.cpp-in-Docker on Linux (override with EVAL_BACKEND=lmstudio|llamacpp).
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FAIL=0

check() {
  local name="$1" out; shift
  # Capture status before piping to head — `cmd | head -1` would report
  # head's (always-zero) exit status and mask a missing tool.
  if out=$("$@" 2>&1); then
    printf '  ok    %-14s %s\n' "$name" "$(printf '%s\n' "$out" | head -1)"
  else
    printf '  MISS  %-14s (%s)\n' "$name" "$*"
    FAIL=1
  fi
}

# Resolve backend the same way the pipeline does: EVAL_BACKEND, then
# config/settings.yaml `backend:`, then auto (macOS -> lmstudio, else llamacpp).
BACKEND="${EVAL_BACKEND:-$(awk '/^backend:/ {print $2}' "$ROOT/config/settings.yaml")}"
if [ -z "$BACKEND" ] || [ "$BACKEND" = "auto" ]; then
  if [ "$(uname -s)" = "Darwin" ]; then BACKEND=lmstudio; else BACKEND=llamacpp; fi
fi

echo "== External tools =="
check python3 python3 --version
check uv uv --version
check vale vale --version
check markdownlint markdownlint --version
check lychee lychee --version
check codespell codespell --version

echo
echo "== Backend: $BACKEND =="
if [ "$BACKEND" = "lmstudio" ]; then
  check lms lms version
  if curl -sf --max-time 5 http://localhost:1234/v1/models > /dev/null; then
    n=$(curl -sf --max-time 5 http://localhost:1234/v1/models | python3 -c \
        'import json,sys; print(len(json.load(sys.stdin)["data"]))')
    echo "  ok    server reachable on localhost:1234 ($n model(s) available)"
  else
    echo "  MISS  LM Studio server not reachable — run: lms server start --port 1234"
    FAIL=1
  fi
else
  # settings.local.yaml (untracked) wins over the tracked default, matching
  # load_settings(); `~` is expanded here since awk yields it literally.
  set -- "$ROOT/config/settings.yaml"
  [ -f "$ROOT/config/settings.local.yaml" ] &&
    set -- "$ROOT/config/settings.local.yaml" "$@"
  COMPOSE_DIR=$(awk '/compose_dir:/ {print $2; exit}' "$@")
  COMPOSE_DIR="${COMPOSE_DIR/#\~/$HOME}"
  MODELS_DIR=/mnt/data-one/llama-models
  check docker docker --version
  check compose docker compose version
  check nvidia-smi nvidia-smi --query-gpu=name --format=csv,noheader
  if [ -f "$COMPOSE_DIR/docker-compose.yml" ]; then
    echo "  ok    compose file    $COMPOSE_DIR/docker-compose.yml"
  else
    echo "  MISS  compose file not found at $COMPOSE_DIR/docker-compose.yml"
    FAIL=1
  fi
  n=$(ls "$MODELS_DIR"/*.gguf 2>/dev/null | wc -l | tr -d ' ')
  if [ "$n" -gt 0 ]; then
    echo "  ok    models         $n gguf file(s) in $MODELS_DIR"
  else
    echo "  MISS  no gguf models in $MODELS_DIR"
    if [ -d "$MODELS_DIR" ] && ! mountpoint -q "$MODELS_DIR" 2>/dev/null \
        && ! mountpoint -q "$(dirname "$MODELS_DIR")" 2>/dev/null; then
      echo "        (directory is empty and not a mounted volume — is the data drive mounted?)"
    fi
    FAIL=1
  fi
  if curl -sf --max-time 5 http://localhost:8080/health > /dev/null; then
    echo "  ok    llama-server reachable on localhost:8080"
  else
    echo "  info  llama-server not running (the pipeline starts it on demand)"
  fi
fi

echo
echo "== Vale style package =="
if [ -d "$ROOT/styles/Google" ]; then
  echo "  ok    Google style package synced"
else
  echo "  MISS  run: (cd styles && vale sync)"
  FAIL=1
fi

exit $FAIL
