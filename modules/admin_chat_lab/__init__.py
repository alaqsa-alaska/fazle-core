"""Admin Chat Lab orchestration helpers.

This module provides a safe, traceable orchestration layer for assistant
requests. It does not mutate production data.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from typing import Awaitable, Callable, Optional
from typing import Any

from fastapi.responses import JSONResponse


@dataclass
class ChatLabResponse:
    answer: str
    sources: list[str] = field(default_factory=list)
    runtime_references: list[str] = field(default_factory=list)
    memory_references: list[str] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    escalation_required: bool = False


@dataclass
class OrchestratorRequest:
    question: str
    requested_model: str | None
    active_model: str
    history: list[dict[str, Any]] = field(default_factory=list)
    mode: str = "general"
    flags: dict[str, bool] = field(default_factory=dict)


@dataclass
class ContextPlan:
    load_memory: bool = True
    load_kb: bool = True
    load_business_tools: bool = True
    load_runtime_tools: bool = False
    load_web_evidence: bool = True


@dataclass
class ToolPlan:
    exact_answer_required: bool = False
    business_tools: list[str] = field(default_factory=list)
    runtime_tools: list[str] = field(default_factory=list)
    max_tools: int = 3


@dataclass
class ProviderSelection:
    provider_chain: list[str] = field(default_factory=lambda: ["ollama", "groq", "github_models"])
    selected_model: str = ""
    reason: str = "default_local_preference"


@dataclass
class EvidenceItem:
    source_type: str
    source_name: str
    authority: str
    deterministic: bool
    data: Any
    timestamp: str | None = None


def classify_discussion(question: str) -> str:
    q = question.lower()
    if any(word in q for word in ("database", "table", "sql", "schema")):
        return "database_discussion"
    if any(word in q for word in ("module", "service", "route", "api")):
        return "module_discussion"
    if any(word in q for word in ("workflow", "process", "approval")):
        return "workflow_discussion"
    if any(word in q for word in ("health", "status", "failure", "down")):
        return "operational_intelligence"
    return "system_discussion"


async def analyze_admin_question(question: str) -> ChatLabResponse:
    from modules.ai_readonly_tools import detect_tools_needed

    discussion_type = classify_discussion(question)
    tools = detect_tools_needed(question)
    return ChatLabResponse(
        answer="Admin Chat Lab analysis prepared. Route this envelope through the LLM provider chain for final wording.",
        sources=["knowledge_base", "runtime_static_inventory"],
        runtime_references=tools,
        trace={"discussion_type": discussion_type, "tools_needed": tools},
        escalation_required=False,
    )


def build_request(
    *,
    question: str,
    requested_model: str | None,
    active_model: str,
    history: list[dict[str, Any]],
    app_context: bool,
    db_context: bool,
    web_context: bool,
    runtime_context: bool = False,
) -> OrchestratorRequest:
    return OrchestratorRequest(
        question=question,
        requested_model=requested_model,
        active_model=active_model,
        history=history,
        mode=_classify_request_mode(question),
        flags={
            "app_context": app_context,
            "db_context": db_context,
            "web_context": web_context,
            "runtime_context": runtime_context,
        },
    )


def build_context_plan(req: OrchestratorRequest) -> ContextPlan:
    return ContextPlan(
        load_memory=True,
        load_kb=req.flags.get("app_context", True),
        load_business_tools=req.flags.get("db_context", True),
        load_runtime_tools=req.flags.get("runtime_context", False),
        load_web_evidence=req.flags.get("web_context", True),
    )


def build_tool_plan(req: OrchestratorRequest, exact_db_answer_tools: set[str]) -> ToolPlan:
    from modules.ai_readonly_tools import detect_tools_needed

    business_tools = detect_tools_needed(req.question)
    exact_needed = any(tool in exact_db_answer_tools for tool in business_tools)
    return ToolPlan(
        exact_answer_required=exact_needed,
        business_tools=business_tools,
        runtime_tools=[],
        max_tools=3,
    )


def build_provider_selection(req: OrchestratorRequest) -> ProviderSelection:
    reason = "requested_model" if req.requested_model else "default_local_preference"
    return ProviderSelection(
        selected_model=req.active_model,
        reason=reason,
    )


async def orchestrate_chat_message(
    *,
    question: str,
    requested_model: str | None,
    active_model: str,
    history: list[dict[str, Any]],
    app_context: bool,
    db_context: bool,
    web_context: bool,
    exact_db_answer_tools: set[str],
    llm_generate: Callable[[str, str, list[dict[str, Any]], str | None], Awaitable[Optional[str]]],
    chat_timeout_s: int,
    logger: Any,
) -> dict[str, Any] | JSONResponse:
    req = build_request(
        question=question,
        requested_model=requested_model,
        active_model=active_model,
        history=history,
        app_context=app_context,
        db_context=db_context,
        web_context=web_context,
    )
    context_plan = build_context_plan(req)
    tool_plan = build_tool_plan(req, exact_db_answer_tools)
    provider_selection = build_provider_selection(req)

    memory_context = await _load_memory_context()
    rag_context, citations, top_score = await _load_rag_context(
        question=req.question,
        enabled=context_plan.load_kb,
        logger=logger,
    )
    db_tool_results, db_tools_used = await _load_business_evidence(
        question=req.question,
        enabled=context_plan.load_business_tools or tool_plan.exact_answer_required,
        tool_plan=tool_plan,
        logger=logger,
    )

    exact_answer = _get_exact_answer(
        db_tool_results=db_tool_results,
        exact_db_answer_tools=exact_db_answer_tools,
    )
    if exact_answer is not None:
        asyncio.create_task(
            _save_memory_record(
                question=req.question,
                answer_summary=exact_answer[:500],
                source_refs=[str(item.get("tool")) for item in db_tool_results if item.get("tool") in exact_db_answer_tools],
            )
        )
        return {
            "answer": exact_answer,
            "model": "deterministic-db-tool",
            "rag_used": bool(rag_context),
            "db_tools_used": db_tools_used,
            "web_fetched": False,
            "citations": citations,
            "top_score": top_score,
            "memory_context_used": bool(memory_context),
        }
    if tool_plan.exact_answer_required:
        return {
            "answer": "এই প্রশ্নের উত্তর live database থেকে দিতে হবে, কিন্তু এখন DB tool থেকে নির্ভরযোগ্য ফল পাওয়া যায়নি। তাই ভুল তথ্য না দিয়ে উত্তর বন্ধ রাখা হলো।",
            "model": "deterministic-db-tool",
            "rag_used": bool(rag_context),
            "db_tools_used": db_tools_used,
            "web_fetched": False,
            "citations": citations,
            "top_score": top_score,
            "memory_context_used": bool(memory_context),
            "error": "exact_db_tool_unavailable",
        }

    web_result = await _load_web_evidence(
        question=req.question,
        enabled=context_plan.load_web_evidence,
        logger=logger,
    )
    evidence = build_evidence_package(
        memory_context=memory_context,
        rag_context=rag_context,
        citations=citations,
        db_tool_results=db_tool_results,
        web_result=web_result,
    )
    combined_context = _render_combined_context(
        memory_context=memory_context,
        rag_context=rag_context,
        db_tool_results=db_tool_results,
        web_result=web_result,
    )

    try:
        answer = await asyncio.wait_for(
            llm_generate(req.question, combined_context, req.history, model=provider_selection.selected_model),
            timeout=chat_timeout_s,
        )
    except asyncio.TimeoutError:
        logger.warning("[chat/message] LLM timeout after %ss model=%s", chat_timeout_s, provider_selection.selected_model)
        return JSONResponse(
            status_code=504,
            content={
                "error": "llm_timeout",
                "answer": "মডেলটি সময়মতো উত্তর দিতে পারেনি। একটু পরে আবার চেষ্টা করুন।",
                "model": provider_selection.selected_model,
                "rag_used": bool(rag_context),
                "timeout_s": chat_timeout_s,
            },
        )

    if not answer:
        answer = combined_context[:500] or "উত্তর পাওয়া যায়নি। অফিসে যোগাযোগ করুন।"

    source_refs = citations + db_tools_used
    if web_result and web_result.get("url"):
        source_refs.append(web_result["url"])
    asyncio.create_task(
        _save_memory_record(
            question=req.question,
            answer_summary=answer[:500],
            source_refs=source_refs,
        )
    )

    return {
        "answer": answer,
        "model": provider_selection.selected_model,
        "rag_used": bool(rag_context),
        "db_tools_used": db_tools_used,
        "web_fetched": bool(web_result and web_result.get("ok")),
        "citations": citations,
        "top_score": top_score,
        "memory_context_used": bool(memory_context),
        "trace": {
            "mode": req.mode,
            "context_plan": asdict(context_plan),
            "tool_plan": asdict(tool_plan),
            "provider_selection": asdict(provider_selection),
            "evidence_count": len(evidence),
        },
    }


def build_evidence_package(
    *,
    memory_context: str,
    rag_context: str,
    citations: list[Any],
    db_tool_results: list[dict[str, Any]],
    web_result: dict[str, Any] | None,
) -> list[EvidenceItem]:
    evidence: list[EvidenceItem] = []
    if memory_context:
        evidence.append(
            EvidenceItem(
                source_type="memory",
                source_name="ollama_memory",
                authority="ai_memory",
                deterministic=False,
                data=memory_context,
            )
        )
    if rag_context:
        evidence.append(
            EvidenceItem(
                source_type="knowledge_base",
                source_name="rag",
                authority="knowledge_base",
                deterministic=False,
                data={"answer": rag_context, "citations": citations},
            )
        )
    for result in db_tool_results:
        evidence.append(
            EvidenceItem(
                source_type="business_tool",
                source_name=str(result.get("tool") or "unknown_tool"),
                authority="postgres_readonly_view",
                deterministic=bool(result.get("answer")),
                data=result.get("data"),
            )
        )
    if web_result and web_result.get("ok"):
        evidence.append(
            EvidenceItem(
                source_type="web",
                source_name=str(web_result.get("url") or "web_fetch"),
                authority="external_readonly_get",
                deterministic=False,
                data=web_result.get("content"),
            )
        )
    return evidence


def _classify_request_mode(question: str) -> str:
    q = question.lower()
    if any(word in q for word in ("service", "health", "status", "port", "runtime", "docker", "nginx")):
        return "runtime"
    if any(word in q for word in ("employee", "payroll", "attendance", "payment", "ledger", "roster")):
        return "business"
    if any(word in q for word in ("memory", "note", "instruction", "remember")):
        return "memory"
    if any(word in q for word in ("investigate", "compare", "why", "error", "issue")):
        return "investigation"
    return "general"


async def _load_memory_context() -> str:
    memory_context = ""
    try:
        from modules import ollama_memory

        recent_qs = await ollama_memory.get_recent_questions(limit=5)
        if recent_qs:
            mem_lines = [
                f"[Memory Q{i+1}] {rq['question'][:120]}: {rq['answer_summary'][:200]}"
                for i, rq in enumerate(recent_qs)
                if rq.get("answer_summary")
            ]
            if mem_lines:
                memory_context = "আগের প্রশ্নের স্মৃতি:\n" + "\n".join(mem_lines)
    except Exception:
        pass
    return memory_context


async def _load_rag_context(*, question: str, enabled: bool, logger: Any) -> tuple[str, list[Any], Any]:
    rag_context = ""
    citations: list[Any] = []
    top_score = None
    if not enabled:
        return rag_context, citations, top_score
    try:
        from modules import rag

        res = await rag.answer(question, k=4, min_score=0.8)
        if res:
            rag_context = res.get("answer", "")
            citations = res.get("citations", [])
            top_score = res.get("top_score")
    except Exception as exc:
        logger.debug("RAG search failed: %s", exc)
    return rag_context, citations, top_score


async def _load_business_evidence(
    *,
    question: str,
    enabled: bool,
    tool_plan: ToolPlan,
    logger: Any,
) -> tuple[list[dict[str, Any]], list[str]]:
    db_tool_results: list[dict[str, Any]] = []
    db_tools_used: list[str] = []
    if not enabled:
        return db_tool_results, db_tools_used
    try:
        from modules import ai_readonly_tools

        for tool_name in tool_plan.business_tools[: tool_plan.max_tools]:
            result = await ai_readonly_tools.run_tool(tool_name, question)
            if result.get("data") is not None:
                db_tool_results.append(result)
                db_tools_used.append(tool_name)
    except Exception as exc:
        logger.debug("ai_readonly_tools failed: %s", exc)
    return db_tool_results, db_tools_used


def _get_exact_answer(
    *,
    db_tool_results: list[dict[str, Any]],
    exact_db_answer_tools: set[str],
) -> str | None:
    for tool_result in db_tool_results:
        if tool_result.get("tool") in exact_db_answer_tools and tool_result.get("answer"):
            return str(tool_result["answer"])
    return None


async def _load_web_evidence(*, question: str, enabled: bool, logger: Any) -> dict[str, Any] | None:
    if not enabled:
        return None
    import re as _re

    url_match = _re.search(r"https?://[^\s\"'<>]+", question)
    if not url_match:
        return None
    try:
        from modules import ai_readonly_tools

        return await ai_readonly_tools.fetch_web_page(url_match.group())
    except Exception as exc:
        logger.debug("web fetch failed: %s", exc)
        return None


def _render_combined_context(
    *,
    memory_context: str,
    rag_context: str,
    db_tool_results: list[dict[str, Any]],
    web_result: dict[str, Any] | None,
) -> str:
    context_parts: list[str] = []
    if memory_context:
        context_parts.append(memory_context)
    if rag_context:
        context_parts.append("জ্ঞানভাণ্ডার (KB):\n" + rag_context)
    for tool_result in db_tool_results:
        tool_name = tool_result["tool"]
        data = tool_result["data"]
        if isinstance(data, list) and data:
            keys = list(data[0].keys())[:6]
            lines = [", ".join(str(row.get(k, "")) for k in keys) for row in data[:15]]
            context_parts.append(f"[{tool_name}]:\n" + "\n".join(lines))
        elif isinstance(data, dict):
            context_parts.append(f"[{tool_name}]: " + ", ".join(f"{k}={v}" for k, v in list(data.items())[:8]))
    if web_result and web_result.get("ok") and web_result.get("content"):
        context_parts.append("ওয়েব তথ্য:\n" + web_result["content"][:1500])
    return "\n\n".join(context_parts)


async def _save_memory_record(*, question: str, answer_summary: str, source_refs: list[str]) -> None:
    try:
        from modules import ollama_memory

        await ollama_memory.record_question(
            question=question,
            answer_summary=answer_summary,
            source_refs=source_refs,
        )
    except Exception:
        pass
