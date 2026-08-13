#!/usr/bin/env bash
set -e

# Start the Ollama server in the background, then pull the model on first
# boot (cached in the ollama_data volume thereafter -- see docker-compose.yml).
ollama serve &
SERVER_PID=$!

echo "Waiting for Ollama server to be ready..."
until ollama list >/dev/null 2>&1; do
  sleep 1
done

if ! ollama list | grep -q "${LLM_MODEL}"; then
  echo "Pulling ${LLM_MODEL} (first run only, cached afterwards)..."
  ollama pull "${LLM_MODEL}"
fi

wait $SERVER_PID
