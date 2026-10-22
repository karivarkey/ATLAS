"""Pydantic request/response schemas for the Atlas v1 API."""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


class ConversationCreate(BaseModel):
    title: str = ""
    model: str = ""
    workspace_id: str = ""


class ConversationPatch(BaseModel):
    title: str | None = None
    model: str | None = None


class MessageSend(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)
    model: str = ""


class AnswerInput(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)


class ApprovalResolve(BaseModel):
    approved: bool
    remember: bool = False


class RetryInput(BaseModel):
    model: str = ""


class ModelInfo(BaseModel):
    id: str
    name: str = ""
    provider: str = ""
    protocol: str = ""  # responses | chat_completions | messages | ollama
    endpoint: str = ""
    context_window: int | None = None
    supports_tools: bool = True
    cached: bool = False
    stale: bool = False


class CatalogResponse(BaseModel):
    models: list[ModelInfo]
    fetched_at: int
    stale: bool
    error: str = ""
    source: str = ""  # live | cache | fallback | ollama


class ExecutionSnapshot(BaseModel):
    id: str
    conversation_id: str
    status: str
    model: str
    workspace_id: str
    current_step: int
    max_steps: int
    pending: dict[str, Any] = {}
    error: str = ""
    updated_at: int
