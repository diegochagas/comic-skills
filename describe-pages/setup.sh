#!/usr/bin/env bash
# describe-pages setup: checks Ollama and pulls the vision model the skill
# uses by default (qwen3-vl:4b, 3.3 GB). Safe to re-run.
set -euo pipefail
MODEL="${DESCRIBE_MODEL:-qwen3-vl:4b}"

if ! command -v ollama >/dev/null 2>&1; then
    echo "ollama is not installed - see https://ollama.com/download (describe-pages needs it)." >&2
    exit 0
fi
if ! curl -sf -m 3 "${OLLAMA_URL:-http://localhost:11434}/api/tags" >/dev/null; then
    echo "Ollama is not running; start it (ollama serve / systemctl --user start ollama) and re-run to pull $MODEL." >&2
    exit 0
fi
if ollama list | awk '{print $1}' | grep -qx "$MODEL"; then
    echo "describe-pages: $MODEL already installed."
else
    echo "describe-pages: pulling $MODEL..."
    ollama pull "$MODEL"
fi
