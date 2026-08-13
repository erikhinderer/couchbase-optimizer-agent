# Serves the local LLM via Ollama's HTTP API (chat: /api/chat, embeddings:
# /api/embeddings) so cluster topology, query text, index definitions, and
# document samples pulled from a customer's production Couchbase cluster
# never leave the Docker network.
FROM ollama/ollama:latest

ENV OLLAMA_HOST=0.0.0.0:11434
ENV LLM_MODEL=qwen3:8b

# Trust an optional corporate/proxy root CA before entrypoint.sh pulls the
# model at container startup. A no-op on machines that don't need it.
COPY certs/ /usr/local/share/ca-certificates/extra/
RUN if find /usr/local/share/ca-certificates/extra -name '*.crt' 2>/dev/null | grep -q .; then \
      apt-get update && \
      apt-get install -y --no-install-recommends ca-certificates && \
      update-ca-certificates && \
      rm -rf /var/lib/apt/lists/*; \
    fi

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 11434
ENTRYPOINT ["/entrypoint.sh"]
