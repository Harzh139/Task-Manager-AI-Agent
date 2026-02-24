"""
workflow.py - LangGraph stateful workflow orchestrating all four agents.

Graph topology:

    [START]
       │
       ▼
   planner ──(error)──────────────────────────────► [END]
       │
       ▼
   executor ──(awaiting_approval)─────────────────► [END]  (caller polls)
       │
       ▼
   monitor ──(error)───────────────────────────────► [END]
       │
       ├──(needs_replan = True)──► planner  (loop, max 2 iterations)
       │
       ▼
  reflection
       │
       ▼
    [END]

State is a plain TypedDict so it can be serialised to JSON between runs.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Annotated

from langgraph.graph import StateGraph, END, START
from langgraph.graph.message import add_messages

from agents.planner import planner_node
from agents.executor import executor_node
from agents.monitor import monitor_node
from agents.reflection import reflection_node

logger = logging.getLogger(__name__)

# ── Shared State Schema ────────────────────────────────────────────────────────

from typing import TypedDict, Optional


class ProductivityState(TypedDict, total=False):
    # ── Input ──────────────────────────────────────────────────
    goal: str
    deadline: Optional[str]
    user_email: str
    credentials: Any               # google.oauth2.credentials.Credentials
    autonomy_level: str            # "manual" | "assisted" | "autonomous"
    approved_tasks: list[str]

    # ── Planner output ─────────────────────────────────────────
    plan: dict[str, Any]

    # ── Executor output ────────────────────────────────────────
    execution_results: list[dict[str, Any]]
    pending_approvals: list[dict[str, Any]]

    # ── Monitor output ─────────────────────────────────────────
    monitor_report: dict[str, Any]
    needs_replan: bool
    productivity_score: int

    # ── Reflection output ──────────────────────────────────────
    reflection: dict[str, Any]

    # ── Shared / cross-agent ───────────────────────────────────
    action_log: list[dict[str, Any]]
    status: str
    error: Optional[str]

    # ── Loop guard ─────────────────────────────────────────────
    replan_count: int


# ── Routing Logic ──────────────────────────────────────────────────────────────

def route_after_planner(state: ProductivityState) -> Literal["executor", "__end__"]:
    """Stop the graph on planner error; otherwise proceed to executor."""
    if state.get("status") == "error":
        logger.warning("Workflow stopping after planner error: %s", state.get("error"))
        return END
    return "executor"


def route_after_executor(state: ProductivityState) -> Literal["monitor", "__end__"]:
    """
    Stop if executor encountered an error or if the system is waiting for user
    approval (manual/assisted mode).  Proceed to monitor otherwise.
    """
    status = state.get("status")
    if status in ("error", "awaiting_approval"):
        logger.info("Workflow pausing after executor (status=%s).", status)
        return END
    return "monitor"


def route_after_monitor(
    state: ProductivityState,
) -> Literal["planner", "reflection", "__end__"]:
    """
    Replan up to 2 additional times if needed; otherwise move to reflection.
    Stop on monitor error.
    """
    if state.get("status") == "error":
        logger.warning("Workflow stopping after monitor error: %s", state.get("error"))
        return END

    replan_count = state.get("replan_count", 0)
    if state.get("needs_replan") and replan_count < 2:
        logger.info("Monitor requested replan (attempt %d).", replan_count + 1)
        return "planner"

    return "reflection"


def route_after_reflection(state: ProductivityState) -> Literal["__end__"]:
    """Reflection is always the last step."""
    return END


# ── Graph Assembly ─────────────────────────────────────────────────────────────

def _increment_replan_count(state: ProductivityState) -> ProductivityState:
    """Middleware node executed before replanning to track iteration count."""
    return {**state, "replan_count": state.get("replan_count", 0) + 1}


def build_workflow() -> StateGraph:
    """
    Construct and compile the LangGraph StateGraph for the productivity system.
    Call .compile() to get a runnable graph.
    """
    builder = StateGraph(ProductivityState)

    # Register nodes
    builder.add_node("planner", planner_node)
    builder.add_node("executor", executor_node)
    builder.add_node("monitor", monitor_node)
    builder.add_node("reflection", reflection_node)

    # Entry
    builder.add_edge(START, "planner")

    # Planner → Executor or END
    builder.add_conditional_edges("planner", route_after_planner)

    # Executor → Monitor or END
    builder.add_conditional_edges("executor", route_after_executor)

    # Monitor → Planner (replan) | Reflection | END
    builder.add_conditional_edges("monitor", route_after_monitor)

    # Reflection → END
    builder.add_conditional_edges("reflection", route_after_reflection)

    return builder


# ── Compiled singleton ─────────────────────────────────────────────────────────

_compiled_graph = None


def get_workflow():
    """Return the compiled LangGraph application (lazy singleton)."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_workflow().compile()
        logger.info("LangGraph workflow compiled successfully.")
    return _compiled_graph


async def run_workflow(initial_state: dict[str, Any]) -> dict[str, Any]:
    """
    Execute the full productivity workflow from *initial_state*.

    Args:
        initial_state: ProductivityState dict with at minimum 'goal' set.

    Returns:
        Final state dict after all nodes have run.
    """
    graph = get_workflow()
    state = {
        "replan_count": 0,
        "action_log": [],
        "approved_tasks": [],
        **initial_state,
    }
    result = await graph.ainvoke(state)
    return dict(result)
