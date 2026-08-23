"""FastAPI app: mock login, chat, action confirmation, and the static chat UI."""

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.agent import core
from app.agent.llm import ModelUnavailable
from app.config import ROOT
from app.db import query, reference_time
from app.models import (
    AccountOption,
    ChatRequest,
    ChatResponse,
    LoginRequest,
    LoginResponse,
)
from app.session import account_for, create_session, list_accounts

app = FastAPI(title="ParcelPilot Support Agent")

# action_id -> session token. The confirm/cancel endpoints resolve the session from the action
# rather than trusting the caller, so one session cannot confirm another session's pending action.
_PENDING: dict[str, str] = {}


def _session(authorization: str | None) -> tuple[str, str]:
    """Returns (token, account_id), rejecting anything without a valid session."""
    token = (authorization or "").removeprefix("Bearer ").strip()
    try:
        return token, account_for(token)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@app.get("/accounts", response_model=list[AccountOption])
def accounts() -> list[dict]:
    return list_accounts()


@app.post("/login", response_model=LoginResponse)
def login(request: LoginRequest) -> LoginResponse:
    try:
        token = create_session(request.account_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    name = query(
        "SELECT account_name FROM accounts WHERE account_id = :a", {"a": request.account_id}
    )[0]["account_name"]
    return LoginResponse(
        session_token=token,
        account_id=request.account_id,
        account_name=name,
        reference_time=reference_time(),
    )


@app.exception_handler(ModelUnavailable)
def model_unavailable(_, exc: ModelUnavailable) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, authorization: str | None = Header(default=None)) -> ChatResponse:
    token, account_id = _session(authorization)
    result = core.start_turn(token, account_id, request.message)
    if result["pending_action"]:
        _PENDING[result["pending_action"]["id"]] = token
    return ChatResponse(**result)


def _decide(action_id: str, authorization: str | None, decision: str) -> ChatResponse:
    token, _ = _session(authorization)
    if _PENDING.get(action_id) != token:
        raise HTTPException(status_code=404, detail="No pending action with that id for this session")
    # A card the conversation has already moved past must not be able to approve the action that
    # replaced it, so a superseded id is refused rather than resumed.
    if core.pending_action_id(token) != action_id:
        del _PENDING[action_id]
        raise HTTPException(
            status_code=409,
            detail="This request is no longer awaiting confirmation. Nothing was created.",
        )
    del _PENDING[action_id]
    return ChatResponse(**core.resume_turn(token, decision, action_id))


@app.post("/actions/{action_id}/confirm", response_model=ChatResponse)
def confirm(action_id: str, authorization: str | None = Header(default=None)) -> ChatResponse:
    return _decide(action_id, authorization, "confirm")


@app.post("/actions/{action_id}/cancel", response_model=ChatResponse)
def cancel(action_id: str, authorization: str | None = Header(default=None)) -> ChatResponse:
    return _decide(action_id, authorization, "cancel")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
