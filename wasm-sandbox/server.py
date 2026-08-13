"""
WASM sandbox service for the Couchbase Optimizer Agent.

Every module run here executes inside a wasmtime Store with:
  - a hard fuel budget (interpreter "steps"), so a runaway or adversarial
    module traps instead of burning CPU forever;
  - zero host imports -- modules are instantiated with an empty import list,
    so anything requiring WASI, network, filesystem, or a host function
    simply fails to instantiate. There is no escape hatch to the container.

Two capabilities are exposed:

  POST /simulate/query-plan-cost
    Runs the built-in cost_model.wat module to give the optimizer a
    before/after "estimated scan cost" signal for a suggested index or query
    rewrite, purely as a sandboxed what-if check before a SAFE_AUTO finding
    is shown to a user with a "tested in sandbox" badge.

  POST /run-wat
    Generic entry point: compiles and runs caller-supplied WAT source under
    the same fuel/no-imports sandbox. Used by the backend's rule engine and
    the agent's own generated validation snippets for anything that isn't
    the built-in cost model (e.g. testing a small formula behind a proposed
    resource-quota change).

This service has no persistence and no outbound network access of its own --
it only ever computes over the numbers it's handed.
"""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any, Literal, Optional

import wasmtime
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

logger = logging.getLogger("wasm-sandbox")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Couchbase Optimizer Agent - WASM Sandbox")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

_executor = ThreadPoolExecutor(max_workers=4)
DEFAULT_FUEL = 50_000_000
MAX_FUEL = 500_000_000
WALL_CLOCK_TIMEOUT_S = 5.0

_COST_MODEL_PATH = os.path.join(os.path.dirname(__file__), "cost_model.wat")
with open(_COST_MODEL_PATH) as f:
    _COST_MODEL_WAT = f.read()

_engine_cfg = wasmtime.Config()
_engine_cfg.consume_fuel = True
_ENGINE = wasmtime.Engine(_engine_cfg)
_COST_MODEL_MODULE = wasmtime.Module(_ENGINE, wasmtime.wat2wasm(_COST_MODEL_WAT))


class SandboxError(Exception):
    pass


def _run_module_sync(module: wasmtime.Module, func_name: str, args: list, fuel: int) -> tuple[Any, int]:
    store = wasmtime.Store(_ENGINE)
    store.set_fuel(fuel)
    try:
        instance = wasmtime.Instance(store, module, [])
    except Exception as exc:  # noqa: BLE001
        raise SandboxError(f"module rejected at instantiation (no host imports are ever provided): {exc}") from exc

    exports = instance.exports(store)
    if func_name not in exports:
        raise SandboxError(f"export '{func_name}' not found in module")
    fn = exports[func_name]
    try:
        result = fn(store, *args)
    except wasmtime.Trap as exc:
        remaining = store.get_fuel() if store.get_fuel() is not None else 0
        raise SandboxError(f"module trapped: {exc} (fuel remaining: {remaining})") from exc
    fuel_left = store.get_fuel() or 0
    return result, fuel - fuel_left


def _run_with_timeout(module: wasmtime.Module, func_name: str, args: list, fuel: int) -> tuple[Any, int]:
    future = _executor.submit(_run_module_sync, module, func_name, args, fuel)
    try:
        return future.result(timeout=WALL_CLOCK_TIMEOUT_S)
    except FutureTimeoutError as exc:
        raise SandboxError("sandbox execution exceeded wall-clock timeout") from exc


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


class PlanScenario(BaseModel):
    uses_index: bool
    selectivity_permille: int = Field(1000, ge=1, le=1000, description="Estimated selectivity in parts-per-1000; 1000 = no filtering benefit.")


class QueryPlanCostRequest(BaseModel):
    doc_count: int = Field(..., ge=0)
    avg_doc_size_bytes: int = Field(..., ge=1)
    before: PlanScenario
    after: PlanScenario
    fuel_budget: int = Field(DEFAULT_FUEL, ge=1000, le=MAX_FUEL)


class QueryPlanCostResponse(BaseModel):
    before_cost_kb: int
    after_cost_kb: int
    improvement_pct: float
    passed: bool
    fuel_consumed: int
    ran_at_ms: int


@app.post("/simulate/query-plan-cost", response_model=QueryPlanCostResponse)
def simulate_query_plan_cost(req: QueryPlanCostRequest) -> QueryPlanCostResponse:
    started = time.time()
    try:
        before_cost, fuel_a = _run_with_timeout(
            _COST_MODEL_MODULE, "estimate_cost",
            [req.doc_count, req.avg_doc_size_bytes, req.before.selectivity_permille, int(req.before.uses_index)],
            req.fuel_budget,
        )
        after_cost, fuel_b = _run_with_timeout(
            _COST_MODEL_MODULE, "estimate_cost",
            [req.doc_count, req.avg_doc_size_bytes, req.after.selectivity_permille, int(req.after.uses_index)],
            req.fuel_budget,
        )
    except SandboxError as exc:
        raise HTTPException(422, str(exc)) from exc

    before_cost = max(int(before_cost), 1)
    after_cost = int(after_cost)
    improvement = round((1 - (after_cost / before_cost)) * 100, 2)

    return QueryPlanCostResponse(
        before_cost_kb=before_cost,
        after_cost_kb=after_cost,
        improvement_pct=improvement,
        passed=after_cost < before_cost,
        fuel_consumed=fuel_a + fuel_b,
        ran_at_ms=int((time.time() - started) * 1000),
    )


class RunWatRequest(BaseModel):
    wat: str = Field(..., description="WebAssembly Text source. Must export the target function and require no imports.")
    function: str
    params: list[int] = Field(default_factory=list)
    fuel_budget: int = Field(DEFAULT_FUEL, ge=1000, le=MAX_FUEL)


class RunWatResponse(BaseModel):
    result: Optional[int]
    fuel_consumed: int
    ran_at_ms: int


@app.post("/run-wat", response_model=RunWatResponse)
def run_wat(req: RunWatRequest) -> RunWatResponse:
    started = time.time()
    try:
        wasm_bytes = wasmtime.wat2wasm(req.wat)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"invalid WAT source: {exc}") from exc

    try:
        module = wasmtime.Module(_ENGINE, wasm_bytes)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"module failed to compile: {exc}") from exc

    try:
        result, fuel_consumed = _run_with_timeout(module, req.function, req.params, req.fuel_budget)
    except SandboxError as exc:
        raise HTTPException(422, str(exc)) from exc

    return RunWatResponse(
        result=int(result) if result is not None else None,
        fuel_consumed=fuel_consumed,
        ran_at_ms=int((time.time() - started) * 1000),
    )
