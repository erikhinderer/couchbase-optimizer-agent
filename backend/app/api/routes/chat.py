"""'Ask the agent' -- the endpoint behind the chat panel."""
from __future__ import annotations

from fastapi import APIRouter

from app.core.llm_agent import ChatAgent
from app.models.schemas import ChatRequest, ChatResponse

router = APIRouter()


@router.post("", response_model=ChatResponse)
async def chat(body: ChatRequest) -> ChatResponse:
    agent = ChatAgent()
    return await agent.chat(body.message, str(body.cluster_id) if body.cluster_id else None)
