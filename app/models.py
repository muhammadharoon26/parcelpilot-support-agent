"""Pydantic request/response schemas for the API surface."""

from pydantic import BaseModel


class LoginRequest(BaseModel):
    account_id: str


class LoginResponse(BaseModel):
    session_token: str
    account_id: str
    account_name: str
    reference_time: str


class ChatRequest(BaseModel):
    message: str


class ToolCall(BaseModel):
    tool: str
    summary: str


class PendingAction(BaseModel):
    id: str
    tool: str
    args: dict
    preview_text: str
    ticket_subject: str | None = None
    ticket_verified: bool | None = None


class ChatResponse(BaseModel):
    reply: str
    tool_calls: list[ToolCall] = []
    pending_action: PendingAction | None = None


class AccountOption(BaseModel):
    account_id: str
    account_name: str
    plan: str
