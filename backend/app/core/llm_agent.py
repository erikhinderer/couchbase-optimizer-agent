"""'Ask the agent' chat -- grounds every answer in the agent's own memory
(short-term/episodic/long-term, all in Couchbase) plus whatever findings are
currently open for the cluster being discussed, rather than answering from
the model's parametric knowledge alone. Any documentation this response
leans on is surfaced back to the caller as DocReferences so the UI can show
the same citation-link pattern used under findings.
"""
from __future__ import annotations

import logging

from app.core.llm_client import LLMClient
from app.core.store import StateStore
from app.memory.couchbase_memory import AgentMemoryStore
from app.models.enums import MemoryTier
from app.models.schemas import ChatResponse, DocReference, Finding

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Couchbase Optimizer Agent, an assistant embedded in a dashboard that \
continuously analyzes Couchbase Enterprise and Capella clusters and suggests or performs \
optimizations. You have three things to ground your answers in, given below: recalled memory \
(short-term, episodic, long-term), currently open findings for the cluster in view, and the \
user's question.

Rules:
- You may explain, recommend, and walk through tradeoffs freely.
- You must never claim to have changed the cluster yourself. Only SAFE_AUTO findings that a human \
has explicitly approved are ever applied, through the approval workflow -- not through this chat.
- If a finding requires an application code change, say so plainly and explain what the team needs \
to change; do not suggest the agent can do it for them.
- You are always told the cluster's access mode below (read-only or read/write) plus, when available, \
the Couchbase roles actually granted to its credential. Respect it: if the cluster is read-only, never \
imply you could approve/apply a change to it right now -- say the operator needs to switch it to \
read/write first (and check the granted roles support that) rather than just proposing the finding. If \
a role-check note flags a mismatch between the declared mode and the granted roles, mention it plainly \
when it's relevant to what the user asked, don't bury it.
- If the cluster in view is a support-bundle snapshot rather than a live connection, say so when it's \
relevant (e.g. if asked to re-run analysis or apply something) -- there's no live cluster behind it, \
so nothing can be applied and nothing will change until a newer bundle is uploaded.
- If you don't have enough grounding to answer confidently, say what you'd need (e.g. "run an \
analysis pass first") rather than guessing.
- Keep answers focused and specific to what was asked; use the findings/memory context, don't \
just restate it.
- For REQUIRES_CODE_CHANGE findings, you may explain and recommend a concrete rewritten query, not \
just describe the problem in the abstract. If a finding below already has a drafted rewrite, use it \
as your basis (you can refine or re-explain it) rather than starting over; if it doesn't, you can \
draft one yourself from the original query shown. Always make clear this is a suggestion for the \
application team to apply themselves -- you're not changing anything by saying it."""


def _format_memories(memories: dict[str, list[dict]]) -> str:
    lines = []
    for tier in ("long_term", "episodic", "short_term"):
        items = memories.get(tier, [])
        if not items:
            continue
        lines.append(f"[{tier} memory]")
        for m in items:
            lines.append(f"  - {m.get('text', '')[:280]}")
    return "\n".join(lines) if lines else "(no relevant memory recalled)"


def _format_findings(findings: list[Finding]) -> str:
    if not findings:
        return "(no open findings for this cluster)"
    lines = []
    for f in findings[:10]:
        lines.append(
            f"  - [{f.severity.value.upper()}/{f.category.value}/{f.action_type.value}] {f.title} "
            f"(status: {f.status.value})"
        )
        # Give the model enough to explain and recommend a rewrite on request
        # without having to invent the original query or the fix from the
        # one-line summary above.
        if f.action_type.value == "requires_code_change":
            sample_statement = (f.evidence or {}).get("sample_statement")
            if f.code_change_guidance:
                lines.append(f"      guidance: {f.code_change_guidance}")
            if sample_statement:
                lines.append(f"      original query: {str(sample_statement)[:400]}")
            if f.suggested_query:
                lines.append(f"      already-drafted rewrite: {f.suggested_query[:600]}")
    return "\n".join(lines)


class ChatAgent:
    def __init__(self) -> None:
        self.llm = LLMClient()
        self.memory = AgentMemoryStore.instance()
        # Shared singleton -- see the comment in analyzer.py's run_analysis()
        # for why a bare StateStore() silently disconnects writes/reads from
        # what the API routes see.
        self.store = StateStore.instance()

    async def chat(self, message: str, cluster_id: str | None = None) -> ChatResponse:
        memories = await self.memory.recall_all_tiers(message, limit_per_tier=4)
        recalled_count = sum(len(v) for v in memories.values())

        findings: list[Finding] = []
        cluster_context = "(no cluster selected)"
        access_context = "(no cluster selected -- no access mode applies)"
        if cluster_id:
            findings = await self.store.list_findings(cluster_id)
            cluster = await self.store.get_cluster(cluster_id)
            if cluster:
                cluster_context = f"{cluster.name} ({cluster.kind.value}), last analyzed: {cluster.last_analyzed_at}"
                if cluster.source_type.value == "support_bundle":
                    access_context = (
                        "Access mode: READ-ONLY -- this is a static Couchbase support bundle snapshot "
                        f"({cluster.bundle_filename or 'uploaded bundle'}, uploaded {cluster.bundle_uploaded_at}), "
                        "not a live connection. There is nothing to approve or apply here, and no fresh data "
                        "to pull -- findings reflect only what that bundle captured at upload time."
                    )
                    if cluster.bundle_parse_note:
                        access_context += f"\nBundle parse note: {cluster.bundle_parse_note}"
                else:
                    mode_label = "READ/WRITE (may approve+apply SAFE_AUTO findings)" if cluster.access_mode.value == "read_write" else "READ-ONLY (may only analyze and suggest -- cannot approve/apply anything)"
                    access_context = f"Access mode: {mode_label}"
                    if cluster.access_mode_note:
                        access_context += f"\nRole-check note: {cluster.access_mode_note}"

        user_prompt = (
            f"Cluster in view: {cluster_context}\n"
            f"{access_context}\n\n"
            f"Recalled memory:\n{_format_memories(memories)}\n\n"
            f"Open findings for this cluster:\n{_format_findings(findings)}\n\n"
            f"User question: {message}"
        )

        try:
            reply = await self.llm.chat(SYSTEM_PROMPT, user_prompt)
        except Exception as exc:  # noqa: BLE001
            logger.error("LLM chat call failed: %s", exc)
            reply = (
                "I couldn't reach the local LLM service just now. Once it's up, I'll be able to answer "
                "using this cluster's findings and my memory of past analysis."
            )

        doc_refs: list[DocReference] = []
        seen_urls: set[str] = set()
        for f in findings:
            for d in f.doc_references:
                if d.url not in seen_urls:
                    doc_refs.append(d)
                    seen_urls.add(d.url)
            if len(doc_refs) >= 4:
                break

        try:
            await self.memory.remember(
                MemoryTier.EPISODIC, "chat_exchange",
                {"message": message, "reply": reply[:500]},
                cluster_id=cluster_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to write chat memory: %s", exc)

        return ChatResponse(reply=reply, doc_references=doc_refs, recalled_memories=recalled_count)
