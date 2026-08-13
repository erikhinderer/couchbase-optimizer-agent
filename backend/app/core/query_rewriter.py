"""Best-effort optimized-query suggestions for REQUIRES_CODE_CHANGE query
findings, via the local LLM.

The rule engine (core/rules/query_rules.py) is good at *detecting* a query
shape problem and explaining it in code_change_guidance, but it's
template-based and doesn't attempt to rewrite the actual query text -- doing
that well needs to reason about the specific statement. This module asks the
local LLM to draft a concrete rewrite, grounded in the same evidence
(sample_statement) and guidance the finding already carries.

Like code_change_guidance, this is explanatory only -- the agent never
applies a query rewrite itself (it can't; the query text lives in the
application, not the cluster). A missing or failed suggestion just means the
finding falls back to code_change_guidance alone, so every call here is
best-effort and never raises.
"""
from __future__ import annotations

import logging

from app.core.llm_client import LLMClient
from app.models.enums import ActionType
from app.models.schemas import Finding

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a Couchbase SQL++ (N1QL) query optimization expert helping rewrite a \
specific slow or inefficient query. You will be given the problem that was detected, a description \
of it, guidance on the general fix, and the original query text.

Rules:
- Output ONLY the rewritten query text, optionally with brief inline SQL comments (--). No prose \
before or after it, and no markdown code fences.
- Preserve the query's original intent, bucket/scope/collection/keyspace names, and result shape as \
closely as possible. Don't invent keyspace names or change what the query is supposed to return.
- If the correct fix depends on information you don't have (e.g. exactly which fields the \
application reads, a specific index name to create first), make the most reasonable assumption and \
mark it with a short inline comment (e.g. `-- replace with the fields the app actually reads`) \
rather than silently guessing wrong.
- If the query genuinely can't be fixed by rewriting it alone (the real fix is structural, like \
denormalizing a document model or adding an index outside the query itself), output the original \
query unchanged with one leading comment line explaining why a rewrite alone doesn't solve it."""


def _user_prompt(finding: Finding, sample_statement: str) -> str:
    return (
        f"Problem: {finding.title}\n"
        f"Details: {finding.description}\n"
        f"General guidance: {finding.code_change_guidance or '(none)'}\n\n"
        f"Original query:\n{sample_statement}"
    )


def _clean(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


async def suggest_optimized_query(finding: Finding) -> str | None:
    """Returns a rewritten query for REQUIRES_CODE_CHANGE findings that carry
    a sample_statement in their evidence, or None if there's nothing to work
    from, the finding isn't a code-change/query-shaped one, or the LLM call
    fails for any reason."""
    if finding.action_type != ActionType.REQUIRES_CODE_CHANGE:
        return None
    sample_statement = (finding.evidence or {}).get("sample_statement")
    if not sample_statement or not str(sample_statement).strip():
        return None

    try:
        raw = await LLMClient().chat(SYSTEM_PROMPT, _user_prompt(finding, str(sample_statement)))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Query rewrite suggestion failed for finding %s: %s", finding.finding_id, exc)
        return None

    cleaned = _clean(raw)
    return cleaned or None
