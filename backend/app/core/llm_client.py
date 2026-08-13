"""Thin client for the local LLM service (Ollama-compatible API).

Local-first by design: cluster topology, query text, index definitions, and
bucket/document metadata pulled from a customer's production cluster are used
to build prompts and embeddings, and none of it should leave the Docker
network. Point LLM_BASE_URL at a different Ollama-compatible endpoint if
desired -- nothing else in the backend assumes a specific provider.
"""
from __future__ import annotations

import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def chat(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self.settings.llm_model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=self.settings.llm_request_timeout_s) as client:
            resp = await client.post(f"{self.settings.llm_base_url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data.get("message", {}).get("content", "").strip()

    async def embed(self, text: str) -> list[float]:
        payload = {"model": self.settings.llm_embedding_model_name, "prompt": text}
        async with httpx.AsyncClient(timeout=self.settings.llm_request_timeout_s) as client:
            resp = await client.post(f"{self.settings.llm_base_url}/api/embeddings", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data.get("embedding", [])
