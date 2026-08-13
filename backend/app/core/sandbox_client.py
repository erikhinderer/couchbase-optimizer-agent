"""Client for the WASM sandbox service (wasm-sandbox/server.py) -- used to
test a suggested optimization's expected impact, or run other short
validation logic, inside a fuel-limited wasmtime runtime with no host
access, before a SAFE_AUTO finding is shown to a user as sandbox-tested."""
from __future__ import annotations

import httpx

from app.config import get_settings


class SandboxClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def simulate_query_plan_cost(
        self,
        *,
        doc_count: int,
        avg_doc_size_bytes: int,
        before_uses_index: bool,
        before_selectivity_permille: int,
        after_uses_index: bool,
        after_selectivity_permille: int,
    ) -> dict:
        payload = {
            "doc_count": doc_count,
            "avg_doc_size_bytes": avg_doc_size_bytes,
            "before": {"uses_index": before_uses_index, "selectivity_permille": before_selectivity_permille},
            "after": {"uses_index": after_uses_index, "selectivity_permille": after_selectivity_permille},
        }
        async with httpx.AsyncClient(timeout=self.settings.wasm_sandbox_timeout_s) as client:
            resp = await client.post(f"{self.settings.wasm_sandbox_url}/simulate/query-plan-cost", json=payload)
            resp.raise_for_status()
            return resp.json()

    async def run_wat(self, wat: str, function: str, params: list[int]) -> dict:
        payload = {"wat": wat, "function": function, "params": params}
        async with httpx.AsyncClient(timeout=self.settings.wasm_sandbox_timeout_s) as client:
            resp = await client.post(f"{self.settings.wasm_sandbox_url}/run-wat", json=payload)
            resp.raise_for_status()
            return resp.json()
