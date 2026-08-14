"""Request/response models for the API."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1, max_length=200)


class IngestionStartResponse(BaseModel):
    thread_id: str


class JobResponse(BaseModel):
    thread_id: str
    state: Literal["paused", "completed"]
    checkpoint: Optional[str] = None       # e.g. "ingestion_approval_request", "answer_approval_request"
    payload: Optional[dict[str, Any]] = None  # present when state == "paused"
    result: Optional[dict[str, Any]] = None   # present when state == "completed"


class ResumeRequest(BaseModel):
    """Pass-through body for Command(resume=...); shape depends on the paused checkpoint."""
    decision: dict[str, Any] = Field(..., description="Arbitrary decision payload for the paused checkpoint")


class QueryStartRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    collection_name: str
    metadata_filter: Optional[dict[str, Any]] = None
    document_id: Optional[str] = Field(
        None, description="Scopes conversational memory + confidentiality authorization to this document (section 17.5)."
    )
