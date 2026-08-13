"""Central configuration, loaded from environment variables (see env.example)."""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    # --- App ---
    app_name: str = "Couchbase Optimizer Agent"
    environment: str = "development"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    # --- Agent memory: Couchbase Enterprise Edition (free developer license) ---
    # This is the agent's OWN short-term / episodic / long-term memory store --
    # never one of the clusters under management.
    memory_cb_connection_string: str = "couchbase://agent-memory"
    memory_cb_username: str = "Administrator"
    memory_cb_password: str = "password"
    memory_cb_bucket: str = "agent_memory"
    memory_cb_scope: str = "agent"
    memory_embedding_dims: int = 4096
    # How many seconds of inactivity before a short-term memory item is
    # considered eligible for consolidation into episodic/long-term memory.
    short_term_consolidation_after_s: int = 900

    # --- Local LLM: served via an Ollama-compatible API ---
    llm_base_url: str = "http://llm-service:11434"
    llm_model_name: str = "qwen3:8b"
    llm_embedding_model_name: str = "qwen3:8b"
    llm_request_timeout_s: int = 120

    # --- WASM sandbox (wasmtime, fuel-limited, no host access) ---
    wasm_sandbox_url: str = "http://wasm-sandbox:8100"
    wasm_sandbox_timeout_s: int = 30

    # --- Continuous analysis loop ---
    analysis_interval_s: int = 300
    completed_requests_lookback: int = 2000

    # --- Documentation reference sources ---
    docs_cache_dir: str = "/data/docs-cache"
    docs_cache_ttl_s: int = 24 * 60 * 60
    allowed_doc_domains: str = "docs.couchbase.com,couchbase.com,developer.couchbase.com"

    @property
    def allowed_doc_domain_list(self) -> list[str]:
        return [d.strip() for d in self.allowed_doc_domains.split(",") if d.strip()]

    # --- Capella Management API (optional, for Capella-hosted clusters) ---
    capella_api_base_url: str = "https://cloudapi.cloud.couchbase.com/v4"
    capella_api_token: str = ""
    capella_org_id: str = ""

    # --- State persistence (registered clusters, findings, optimizations) ---
    state_file: str = "/data/state/state.json"

    # --- Support bundle uploads (offline analysis, no live connection) ---
    bundle_storage_dir: str = "/data/bundles"
    bundle_max_size_mb: int = 500


@lru_cache
def get_settings() -> Settings:
    return Settings()
