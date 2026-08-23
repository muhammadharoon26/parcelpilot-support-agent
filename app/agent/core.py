"""The LangGraph agent: an agent/tools/confirmation cycle where the action tool pauses on interrupt()."""

import json
import operator
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

from app.agent import llm
from app.agent.system_prompt import SYSTEM_PROMPT
from app.agent.tool_schemas import TOOL_SCHEMAS
from app.tools import account_data, actions, document_search

ACTION_TOOL = "create_escalation"
NO_ANSWER = "Sorry, I couldn't produce an answer for that. Please rephrase, or ask to be put through to a human."


class AgentState(TypedDict):
    messages: Annotated[list[dict], operator.add]
    account_id: str
    pending_action: dict | None
    # Read-only tool results wait here while the action tool is held for confirmation, because
    # Claude requires every tool_use in a turn to be answered in one message.
    results_buffer: list[Any]
    tool_log: Annotated[list[dict], operator.add]


def _summarise(name: str, args: dict, result: dict) -> str:
    """One line per tool call for the UI's activity badge."""
    if name == "search_documents":
        sources = {r["source_file"] for r in result.get("results", [])}
        return f"\"{args.get('query', '')}\" -> {len(result.get('results', []))} chunks from {len(sources)} source(s)"
    if name == "query_account_data":
        detail = ", ".join(f"{k}={v}" for k, v in (args.get("params") or {}).items())
        return f"{args.get('query_type')}({detail}) -> {'error' if 'error' in result else 'ok'}"
    return result.get("status", "pending confirmation")


def _run_tool(account_id: str, name: str, args: dict) -> dict:
    if name == "search_documents":
        return document_search.search(account_id, args.get("query", ""))
    if name == "query_account_data":
        return account_data.run(account_id, args.get("query_type", ""), args.get("params") or {})
    return {"error": f"Unknown tool: {name}"}


def _tool_result(call: dict, payload: dict) -> dict:
    # `name` is carried for providers that match a result to its call by tool name rather than by id.
    return {
        "type": "tool_result",
        "tool_use_id": call["id"],
        "name": call["name"],
        "content": json.dumps(payload, default=str),
    }


def agent_node(state: AgentState) -> dict:
    content = llm.complete(SYSTEM_PROMPT, state["messages"], TOOL_SCHEMAS)
    return {"messages": [{"role": "assistant", "content": content}]}


def tools_node(state: AgentState) -> dict:
    account_id = state["account_id"]
    calls = [b for b in state["messages"][-1]["content"] if b["type"] == "tool_use"]

    results: list[Any] = []
    pending_action, log = None, []

    for call in calls:
        if call["name"] == ACTION_TOOL:
            # Not executed here. A placeholder holds this slot until the user confirms.
            pending_action = actions.preview(account_id, call["input"]) | {"call": call}
            results.append(None)
            log.append({"tool": ACTION_TOOL, "summary": "prepared, awaiting user confirmation"})
        else:
            result = _run_tool(account_id, call["name"], call["input"])
            results.append(_tool_result(call, result))
            log.append({"tool": call["name"], "summary": _summarise(call["name"], call["input"], result)})

    if pending_action:
        return {"pending_action": pending_action, "results_buffer": results, "tool_log": log}
    return {"messages": [{"role": "user", "content": results}], "results_buffer": [], "tool_log": log}


def human_confirmation_node(state: AgentState) -> dict:
    """Pauses the graph. Execution resumes here with the user's decision, and only then does the write happen."""
    pending = state["pending_action"]
    decision = interrupt({"pending_action": pending})

    # The approval must name the action it approved. Without this check, a stale confirmation card
    # left on screen would execute whatever action happens to be pending now, which is not what the
    # user agreed to.
    if not isinstance(decision, dict):
        decision = {"decision": decision, "action_id": pending["id"]}
    approved = decision.get("decision") == "confirm" and decision.get("action_id") == pending["id"]

    if approved:
        result = actions.execute(pending)
    elif decision.get("decision") == "confirm":
        result = actions.declined("The confirmation did not match this pending action, so nothing was created.")
    else:
        result = actions.declined()
    results = [r if r is not None else _tool_result(pending["call"], result) for r in state["results_buffer"]]

    return {
        "messages": [{"role": "user", "content": results}],
        "pending_action": None,
        "results_buffer": [],
        "tool_log": [{"tool": ACTION_TOOL, "summary": result["status"]}],
    }


def route_after_agent(state: AgentState) -> str:
    return "tools" if any(b["type"] == "tool_use" for b in state["messages"][-1]["content"]) else "end"


def route_after_tools(state: AgentState) -> str:
    return "confirm" if state.get("pending_action") else "continue"


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.add_node("human_confirmation", human_confirmation_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", route_after_agent, {"tools": "tools", "end": END})
    graph.add_conditional_edges(
        "tools", route_after_tools, {"confirm": "human_confirmation", "continue": "agent"}
    )
    graph.add_edge("human_confirmation", "agent")
    return graph.compile(checkpointer=MemorySaver())


GRAPH = build_graph()


def _final_text(state: dict) -> str:
    for message in reversed(state.get("messages", [])):
        if message["role"] == "assistant":
            text = "\n".join(b["text"] for b in message["content"] if b["type"] == "text")
            if text.strip():
                return text
    return ""


def _interrupt_payload(result: dict) -> dict | None:
    interrupts = result.get("__interrupt__")
    return interrupts[0].value["pending_action"] if interrupts else None


def _invoke(payload: Any, session_id: str, log_offset: int) -> dict:
    config = {"configurable": {"thread_id": session_id}}
    result = GRAPH.invoke(payload, config=config)
    pending = _interrupt_payload(result)
    state = GRAPH.get_state(config).values
    # A model can return no text at all; say something rather than rendering an empty bubble.
    reply = "" if pending else (_final_text(state) or NO_ANSWER)
    return {
        "reply": reply,
        "tool_calls": state.get("tool_log", [])[log_offset:],
        "pending_action": pending,
    }


def start_turn(session_id: str, account_id: str, message: str) -> dict:
    offset = len(GRAPH.get_state({"configurable": {"thread_id": session_id}}).values.get("tool_log", []))
    payload = {
        "messages": [{"role": "user", "content": [{"type": "text", "text": message}]}],
        "account_id": account_id,
        "pending_action": None,
        "results_buffer": [],
        "tool_log": [],
    }
    return _invoke(payload, session_id, offset)


def pending_action_id(session_id: str) -> str | None:
    """The action currently awaiting confirmation on this session, if any."""
    pending = GRAPH.get_state({"configurable": {"thread_id": session_id}}).values.get("pending_action")
    return pending["id"] if pending else None


def resume_turn(session_id: str, decision: str, action_id: str) -> dict:
    offset = len(GRAPH.get_state({"configurable": {"thread_id": session_id}}).values.get("tool_log", []))
    payload = Command(resume={"decision": decision, "action_id": action_id})
    return _invoke(payload, session_id, offset)
