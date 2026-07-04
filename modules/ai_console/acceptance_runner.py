"""Phase 8.5 pre-production acceptance runner."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from httpx import ASGITransport, AsyncClient

from app.config import get_settings
from modules.ai_console.live_adapters import (
    AIMemoryAdapter,
    CANONICAL_RUNTIME_INVENTORY_PATH,
    ReadonlyBusinessAdapter,
    ReadonlyRuntimeAdapter,
)
from modules.ai_console.orchestrator import orchestrate_console_request
from modules.ai_console.provider_registry import ProviderRegistry


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_PATH = ROOT.parent / "PRE_PRODUCTION_ACCEPTANCE_REPORT.md"
DEFAULT_JSON_PATH = ROOT / "reports" / "certification" / "pre_production_acceptance_report.json"
DEFAULT_RUNTIME_TOOLS = [
    "RT-001",
    "RT-002",
    "RT-003",
    "RT-004",
    "RT-009",
    "RT-010",
    "RT-011",
    "RT-012",
    "RT-013",
]
DEFAULT_BUSINESS_TOOLS = [
    ("BUS-001", {"query": "017"}),
    ("BUS-003", {"question": "আজ payroll summary কী?"}),
    ("BUS-004", {"question": "আজ attendance summary কী?"}),
    ("BUS-005", {"question": "আজ escort summary কী?"}),
    ("BUS-008", {"question": "escort workflow explain করো"}),
    ("BUS-009", {"question": "recent conversation history"}),
]


@dataclass
class AcceptanceCheck:
    check_id: str
    title: str
    status: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    critical: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AcceptanceSuite:
    suite_id: str
    title: str
    status: str
    summary: str
    checks: list[AcceptanceCheck] = field(default_factory=list)
    critical: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "checks": [check.to_dict() for check in self.checks],
        }


@dataclass
class AcceptanceReport:
    generated_at: str
    verdict: str
    status: str
    feature_flag_expected: str
    deployment_status: str
    recommendation: str
    suites: list[AcceptanceSuite]

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "verdict": self.verdict,
            "status": self.status,
            "feature_flag_expected": self.feature_flag_expected,
            "deployment_status": self.deployment_status,
            "recommendation": self.recommendation,
            "suites": [suite.to_dict() for suite in self.suites],
        }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _suite_status(checks: list[AcceptanceCheck], *, critical: bool = False) -> str:
    statuses = {item.status for item in checks}
    if "FAIL" in statuses:
        return "FAIL"
    if critical and "SKIP" in statuses:
        return "PARTIAL PASS"
    if "WARN" in statuses or "SKIP" in statuses:
        return "PARTIAL PASS"
    return "PASS"


def _suite_summary(checks: list[AcceptanceCheck]) -> str:
    counts = {"PASS": 0, "PARTIAL PASS": 0, "WARN": 0, "FAIL": 0, "SKIP": 0}
    for item in checks:
        counts[item.status] = counts.get(item.status, 0) + 1
    parts = [f"{label}={count}" for label, count in counts.items() if count]
    return ", ".join(parts) or "No checks executed."


def determine_report_verdict(suites: list[AcceptanceSuite]) -> tuple[str, str, str]:
    critical_suites = [suite for suite in suites if suite.critical]
    all_statuses = {suite.status for suite in suites}
    critical_statuses = {suite.status for suite in critical_suites}
    if "FAIL" in critical_statuses:
        return ("FAIL", "BLOCKS RELEASE", "Return to engineering before Phase 9 rollout.")
    if "PARTIAL PASS" in critical_statuses or "FAIL" in all_statuses:
        return ("PARTIAL PASS", "REQUIRES OWNER REVIEW", "Resolve critical warnings before release gate approval.")
    if "PARTIAL PASS" in all_statuses:
        return ("PARTIAL PASS", "OWNER REVIEW REQUIRED", "Owner may accept gaps or send targeted fixes back to engineering.")
    return ("PASS", "READY FOR RELEASE GATE", "Proceed to release gate review while keeping AI Console feature flag OFF.")


def render_markdown_report(report: AcceptanceReport) -> str:
    lines = [
        "# PRE PRODUCTION ACCEPTANCE REPORT",
        "",
        f"Generated: {report.generated_at}",
        f"Verdict: {report.verdict}",
        f"Status: {report.status}",
        f"Deployment: {report.deployment_status}",
        f"Feature Flag Expected: {report.feature_flag_expected}",
        "",
        "## Recommendation",
        "",
        report.recommendation,
        "",
    ]
    for suite in report.suites:
        lines.extend(
            [
                f"## {suite.title}",
                "",
                f"- Suite ID: `{suite.suite_id}`",
                f"- Status: `{suite.status}`",
                f"- Critical: `{suite.critical}`",
                f"- Summary: {suite.summary}",
                "",
            ]
        )
        for check in suite.checks:
            lines.extend(
                [
                    f"### {check.title}",
                    "",
                    f"- Check ID: `{check.check_id}`",
                    f"- Status: `{check.status}`",
                    f"- Critical: `{check.critical}`",
                    f"- Summary: {check.summary}",
                ]
            )
            if check.warnings:
                lines.append(f"- Warnings: {json.dumps(check.warnings, ensure_ascii=False)}")
            if check.errors:
                lines.append(f"- Errors: {json.dumps(check.errors, ensure_ascii=False)}")
            if check.details:
                lines.extend(["", "```json", json.dumps(check.details, ensure_ascii=False, indent=2), "```"])
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


async def _await_with_timeout(awaitable, timeout_s: float, label: str):
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout_s)
    except TimeoutError:
        raise TimeoutError(f"{label} timed out after {timeout_s:.0f}s")


class PreProductionAcceptanceRunner:
    def __init__(
        self,
        *,
        report_path: Path | None = None,
        json_path: Path | None = None,
        allow_memory_write: bool = False,
        include_live_llm: bool = False,
    ) -> None:
        self.report_path = report_path or DEFAULT_REPORT_PATH
        self.json_path = json_path or DEFAULT_JSON_PATH
        self.allow_memory_write = allow_memory_write
        self.include_live_llm = include_live_llm
        self.settings = get_settings()

    async def run(self) -> AcceptanceReport:
        suites = [
            await self.run_runtime_suite(),
            await self.run_business_suite(),
            await self.run_memory_suite(),
            await self.run_provider_suite(),
            await self.run_ui_suite(),
            await self.run_regression_suite(),
            await self.run_ai_quality_suite(),
        ]
        verdict, status, recommendation = determine_report_verdict(suites)
        report = AcceptanceReport(
            generated_at=utc_now_iso(),
            verdict=verdict,
            status=status,
            feature_flag_expected="OFF",
            deployment_status="NOT STARTED",
            recommendation=recommendation,
            suites=suites,
        )
        self.write_report(report)
        return report

    async def run_runtime_suite(self) -> AcceptanceSuite:
        adapter = ReadonlyRuntimeAdapter()
        checks: list[AcceptanceCheck] = []
        for tool_id in DEFAULT_RUNTIME_TOOLS:
            try:
                result = await _await_with_timeout(
                    adapter.fetch(tool_id, {"question": "phase 8.5 runtime acceptance probe"}),
                    20.0,
                    tool_id,
                )
                ok = result.status in {"ok", "partial"}
            except Exception as exc:
                checks.append(
                    AcceptanceCheck(
                        check_id=tool_id,
                        title=tool_id,
                        status="FAIL",
                        summary="Runtime acceptance check did not complete cleanly.",
                        errors=[str(exc)],
                        critical=True,
                    )
                )
                continue
            checks.append(
                AcceptanceCheck(
                    check_id=tool_id,
                    title=result.tool_name,
                    status="PASS" if ok else "FAIL",
                    summary=f"{result.status} / {result.confidence}",
                    details={
                        "tool_id": result.tool_id,
                        "status": result.status,
                        "confidence": result.confidence,
                        "authority": result.authority,
                        "source": result.source,
                    },
                    warnings=result.warnings,
                    errors=result.errors,
                    critical=True,
                )
            )
        return AcceptanceSuite(
            suite_id="PPA-RUNTIME",
            title="Runtime Acceptance",
            status=_suite_status(checks, critical=True),
            summary=_suite_summary(checks),
            checks=checks,
            critical=True,
        )

    async def run_business_suite(self) -> AcceptanceSuite:
        adapter = ReadonlyBusinessAdapter()
        checks: list[AcceptanceCheck] = []
        for tool_id, payload in DEFAULT_BUSINESS_TOOLS:
            try:
                result = await _await_with_timeout(adapter.fetch(tool_id, payload), 20.0, tool_id)
                ok = result.status in {"ok", "partial"}
            except Exception as exc:
                checks.append(
                    AcceptanceCheck(
                        check_id=tool_id,
                        title=tool_id,
                        status="FAIL",
                        summary="Business acceptance check did not complete cleanly.",
                        errors=[str(exc)],
                        critical=True,
                    )
                )
                continue
            checks.append(
                AcceptanceCheck(
                    check_id=tool_id,
                    title=result.tool_name,
                    status="PASS" if ok else "FAIL",
                    summary=f"{result.status} / {result.confidence}",
                    details={
                        "tool_id": result.tool_id,
                        "status": result.status,
                        "confidence": result.confidence,
                        "authority": result.authority,
                        "source": result.source,
                    },
                    warnings=result.warnings,
                    errors=result.errors,
                    critical=True,
                )
            )
        return AcceptanceSuite(
            suite_id="PPA-BUSINESS",
            title="Business Acceptance",
            status=_suite_status(checks, critical=True),
            summary=_suite_summary(checks),
            checks=checks,
            critical=True,
        )

    async def run_memory_suite(self) -> AcceptanceSuite:
        adapter = AIMemoryAdapter()
        checks: list[AcceptanceCheck] = []
        if not self.allow_memory_write:
            checks.append(
                AcceptanceCheck(
                    check_id="MEM-001-save-recall",
                    title="Owner instruction memory save/recall",
                    status="SKIP",
                    summary="Memory write test skipped. Re-run with --allow-memory-write for full acceptance.",
                    details={"allowed": False},
                )
            )
        else:
            token = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            note = f"Phase 8.5 acceptance note {token}"
            saved = await _await_with_timeout(adapter.fetch("MEM-001", {"action": "save", "text": note}), 20.0, "MEM-001 save")
            recalled = await _await_with_timeout(adapter.fetch("MEM-001", {"action": "recall"}), 20.0, "MEM-001 recall")
            records = (recalled.data or {}).get("records") or []
            matched = any(note in (item.get("fact_text") or "") for item in records if isinstance(item, dict))
            checks.append(
                AcceptanceCheck(
                    check_id="MEM-001-save-recall",
                    title="Owner instruction memory save/recall",
                    status="PASS" if saved.status == "ok" and recalled.status == "ok" and matched else "FAIL",
                    summary=f"save={saved.status}, recall={recalled.status}, matched={matched}",
                    details={
                        "saved_status": saved.status,
                        "recalled_status": recalled.status,
                        "matched": matched,
                    },
                    warnings=saved.warnings + recalled.warnings,
                    errors=saved.errors + recalled.errors,
                )
            )
        return AcceptanceSuite(
            suite_id="PPA-MEMORY",
            title="Memory Acceptance",
            status=_suite_status(checks),
            summary=_suite_summary(checks),
            checks=checks,
        )

    async def run_provider_suite(self) -> AcceptanceSuite:
        registry = ProviderRegistry()
        data = await registry.list_models()
        checks: list[AcceptanceCheck] = []
        providers = data.get("providers") or {}
        for provider_name in ("ollama", "github_models", "groq"):
            meta = providers.get(provider_name) or {}
            status = str(meta.get("status") or "unknown")
            okay = status in {"ok", "disabled", "unknown"}
            checks.append(
                AcceptanceCheck(
                    check_id=f"PROV-{provider_name}",
                    title=f"{provider_name} provider status",
                    status="PASS" if okay else "FAIL",
                    summary=f"status={status}",
                    details=meta,
                    critical=True,
                )
            )
        leak = any("key" in json.dumps(item).lower() and "api" in json.dumps(item).lower() for item in data.get("models", []))
        checks.append(
            AcceptanceCheck(
                check_id="PROV-secret-leak",
                title="Provider registry secret exposure",
                status="FAIL" if leak else "PASS",
                summary="No provider secret fields exposed." if not leak else "Potential provider secret exposure detected.",
                details={"model_count": len(data.get("models", [])), "primary_provider": data.get("primary_provider")},
                critical=True,
            )
        )
        return AcceptanceSuite(
            suite_id="PPA-PROVIDER",
            title="Provider Acceptance",
            status=_suite_status(checks, critical=True),
            summary=_suite_summary(checks),
            checks=checks,
            critical=True,
        )

    async def run_ui_suite(self) -> AcceptanceSuite:
        html = (ROOT / "app" / "static" / "open-chat.html").read_text(encoding="utf-8")
        required_tokens = {
            "markdown": "markdownToHtml",
            "evidence": "evidence-list",
            "traceability": "trace-list",
            "confidence": "confidence",
            "provider_badge": "provider-pill",
            "voice": "voice-btn",
            "file_upload": "file-input",
        }
        checks: list[AcceptanceCheck] = []
        for label, token in required_tokens.items():
            checks.append(
                AcceptanceCheck(
                    check_id=f"UI-{label}",
                    title=f"UI token: {label}",
                    status="PASS" if token in html else "FAIL",
                    summary=f"Token `{token}` {'found' if token in html else 'missing'} in open-chat.html",
                    details={"token": token},
                )
            )
        return AcceptanceSuite(
            suite_id="PPA-UI",
            title="UI Acceptance",
            status=_suite_status(checks),
            summary=_suite_summary(checks),
            checks=checks,
        )

    async def run_regression_suite(self) -> AcceptanceSuite:
        from app.main import app

        checks: list[AcceptanceCheck] = []
        headers = {"X-Internal-Key": self.settings.internal_api_key}
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers=headers,
            timeout=60.0,
        ) as client:
            health = await _await_with_timeout(client.get("/health"), 20.0, "/health")
            checks.append(
                AcceptanceCheck(
                    check_id="REG-health",
                    title="/health",
                    status="PASS" if health.status_code in {200, 503} else "FAIL",
                    summary=f"HTTP {health.status_code}",
                    details=health.json(),
                    critical=True,
                )
            )
            models = await _await_with_timeout(client.get("/chat/models"), 20.0, "/chat/models")
            checks.append(
                AcceptanceCheck(
                    check_id="REG-chat-models",
                    title="/chat/models",
                    status="PASS" if models.status_code == 200 else "FAIL",
                    summary=f"HTTP {models.status_code}",
                    details=models.json() if models.status_code == 200 else {"body": models.text[:400]},
                    critical=True,
                )
            )
            open_chat = await _await_with_timeout(client.get("/open-chat"), 20.0, "/open-chat")
            checks.append(
                AcceptanceCheck(
                    check_id="REG-open-chat",
                    title="/open-chat",
                    status="PASS" if open_chat.status_code == 200 and "Fazle AI Operating Console" in open_chat.text else "FAIL",
                    summary=f"HTTP {open_chat.status_code}",
                    details={"has_console_title": "Fazle AI Operating Console" in open_chat.text},
                    critical=True,
                )
            )
            runtime_nodes = await _await_with_timeout(
                client.get(CANONICAL_RUNTIME_INVENTORY_PATH),
                20.0,
                CANONICAL_RUNTIME_INVENTORY_PATH,
            )
            checks.append(
                AcceptanceCheck(
                    check_id="REG-runtime-current",
                    title=CANONICAL_RUNTIME_INVENTORY_PATH,
                    status="PASS" if runtime_nodes.status_code == 200 else "FAIL",
                    summary=f"HTTP {runtime_nodes.status_code}",
                    details=runtime_nodes.json() if runtime_nodes.status_code == 200 else {"body": runtime_nodes.text[:400]},
                    critical=True,
                )
            )
            try:
                chat_message = await _await_with_timeout(
                    client.post(
                        "/chat/message",
                        json={
                            "q": "Say: ok",
                            "model": "qwen2.5:3b",
                            "app_context": False,
                            "db_context": False,
                            "web_context": False,
                            "history": [],
                        },
                    ),
                    120.0,
                    "/chat/message",
                )
                chat_payload = chat_message.json() if chat_message.status_code == 200 else {"body": chat_message.text[:400]}
                feature_path = chat_payload.get("feature_path") if isinstance(chat_payload, dict) else None
                chat_status = "PASS" if chat_message.status_code == 200 and feature_path == "admin_chat_lab" else "FAIL"
                chat_summary = f"HTTP {chat_message.status_code}, feature_path={feature_path}"
                chat_errors: list[str] = []
                chat_details = chat_payload if isinstance(chat_payload, dict) else {"body": str(chat_payload)}
            except Exception as exc:
                chat_status = "FAIL"
                chat_summary = "Legacy chat smoke did not complete cleanly."
                chat_errors = [str(exc)]
                chat_details = {"feature_path_expected": "admin_chat_lab"}
            checks.append(
                AcceptanceCheck(
                    check_id="REG-chat-message",
                    title="/chat/message legacy smoke",
                    status=chat_status,
                    summary=chat_summary,
                    details=chat_details,
                    errors=chat_errors,
                    critical=True,
                )
            )
        return AcceptanceSuite(
            suite_id="PPA-REGRESSION",
            title="Regression Safety Acceptance",
            status=_suite_status(checks, critical=True),
            summary=_suite_summary(checks),
            checks=checks,
            critical=True,
        )

    async def run_ai_quality_suite(self) -> AcceptanceSuite:
        checks: list[AcceptanceCheck] = []
        prompts = [
            ("AI-runtime", "আমার VPS-এ কী কী Docker container চলছে?"),
            ("AI-business", "আজ payroll summary কী?"),
            ("AI-hallucination", "আজ ১৫টা bridge disconnected।"),
        ]
        if not self.include_live_llm:
            checks.append(
                AcceptanceCheck(
                    check_id="AI-live-llm",
                    title="Live LLM acceptance prompts",
                    status="SKIP",
                    summary="Live LLM prompts skipped. Re-run with --include-live-llm for owner-facing answer validation.",
                    details={"prompt_count": len(prompts)},
                )
            )
        else:
            for check_id, prompt in prompts:
                response = await _await_with_timeout(
                    orchestrate_console_request(
                        prompt,
                        requested_model="qwen2.5:3b",
                        app_context=True,
                        db_context=True,
                        web_context=False,
                        include_provider_response=True,
                    ),
                    180.0,
                    f"AI quality prompt {check_id}",
                )
                evidence = response.evidence or {}
                answer = (response.provider_result or {}).get("response") or evidence.get("response") or ""
                confidence = evidence.get("confidence") or "Unknown"
                if check_id == "AI-hallucination":
                    safe = confidence != "Verified" or "unknown" in answer.lower() or "যাচাই" in answer.lower()
                    status = "PASS" if safe else "WARN"
                    summary = f"confidence={confidence}"
                else:
                    status = "PASS" if answer.strip() else "FAIL"
                    summary = f"answer_length={len(answer.strip())}, confidence={confidence}"
                checks.append(
                    AcceptanceCheck(
                        check_id=check_id,
                        title=prompt,
                        status=status,
                        summary=summary,
                        details={
                            "confidence": confidence,
                            "answer_preview": answer[:500],
                            "traceability": response.traceability,
                            "selected_tools": response.selected_tools,
                        },
                    )
                )
        return AcceptanceSuite(
            suite_id="PPA-AI-QUALITY",
            title="AI Quality Acceptance",
            status=_suite_status(checks),
            summary=_suite_summary(checks),
            checks=checks,
        )

    def write_report(self, report: AcceptanceReport) -> None:
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text(render_markdown_report(report), encoding="utf-8")
        self.json_path.parent.mkdir(parents=True, exist_ok=True)
        self.json_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Phase 8.5 pre-production acceptance checks.")
    parser.add_argument("--report-path", default=str(DEFAULT_REPORT_PATH), help="Markdown report output path.")
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH), help="JSON report output path.")
    parser.add_argument("--allow-memory-write", action="store_true", help="Allow AI memory save/recall acceptance test.")
    parser.add_argument("--include-live-llm", action="store_true", help="Run owner-facing live LLM acceptance prompts.")
    return parser


async def _amain(args: argparse.Namespace) -> int:
    runner = PreProductionAcceptanceRunner(
        report_path=Path(args.report_path),
        json_path=Path(args.json_path),
        allow_memory_write=bool(args.allow_memory_write),
        include_live_llm=bool(args.include_live_llm),
    )
    report = await runner.run()
    print(f"[phase-8.5] verdict={report.verdict} status={report.status} report={runner.report_path}")
    return 0 if report.verdict != "FAIL" else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return asyncio.run(_amain(args))


__all__ = [
    "AcceptanceCheck",
    "AcceptanceReport",
    "AcceptanceSuite",
    "DEFAULT_JSON_PATH",
    "DEFAULT_REPORT_PATH",
    "PreProductionAcceptanceRunner",
    "determine_report_verdict",
    "main",
    "render_markdown_report",
]
