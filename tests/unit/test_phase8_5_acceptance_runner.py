from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_phase8_5_report_verdicts_cover_release_states():
    from modules.ai_console.acceptance_runner import (
        AcceptanceSuite,
        determine_report_verdict,
    )

    verdict = determine_report_verdict(
        [
            AcceptanceSuite("crit", "Critical", "PASS", "ok", critical=True),
            AcceptanceSuite("advisory", "Advisory", "PARTIAL PASS", "warn"),
        ]
    )
    assert verdict[0] == "PARTIAL PASS"

    fail = determine_report_verdict(
        [
            AcceptanceSuite("crit", "Critical", "FAIL", "bad", critical=True),
        ]
    )
    assert fail[0] == "FAIL"

    passed = determine_report_verdict(
        [
            AcceptanceSuite("crit", "Critical", "PASS", "ok", critical=True),
            AcceptanceSuite("ui", "UI", "PASS", "ok"),
        ]
    )
    assert passed[0] == "PASS"


def test_phase8_5_markdown_report_contains_suite_and_check_details():
    from modules.ai_console.acceptance_runner import (
        AcceptanceCheck,
        AcceptanceReport,
        AcceptanceSuite,
        render_markdown_report,
    )

    report = AcceptanceReport(
        generated_at="2026-06-30T00:00:00+00:00",
        verdict="PASS",
        status="READY FOR RELEASE GATE",
        feature_flag_expected="OFF",
        deployment_status="NOT STARTED",
        recommendation="Proceed to release gate review.",
        suites=[
            AcceptanceSuite(
                suite_id="PPA-UI",
                title="UI Acceptance",
                status="PASS",
                summary="PASS=1",
                checks=[
                    AcceptanceCheck(
                        check_id="UI-markdown",
                        title="Markdown visible",
                        status="PASS",
                        summary="Rendered",
                        details={"token": "markdownToHtml"},
                    )
                ],
            )
        ],
    )

    rendered = render_markdown_report(report)
    assert "PRE PRODUCTION ACCEPTANCE REPORT" in rendered
    assert "UI Acceptance" in rendered
    assert "UI-markdown" in rendered
    assert '"token": "markdownToHtml"' in rendered


@pytest.mark.asyncio
async def test_phase8_5_runner_writes_consolidated_report(tmp_path, monkeypatch):
    from modules.ai_console.acceptance_runner import PreProductionAcceptanceRunner

    async def fake_runtime_suite(self):
        from modules.ai_console.acceptance_runner import AcceptanceCheck, AcceptanceSuite
        return AcceptanceSuite(
            suite_id="PPA-RUNTIME",
            title="Runtime Acceptance",
            status="PASS",
            summary="PASS=1",
            checks=[AcceptanceCheck("RT-001", "systemd", "PASS", "ok", critical=True)],
            critical=True,
        )

    async def fake_business_suite(self):
        from modules.ai_console.acceptance_runner import AcceptanceCheck, AcceptanceSuite
        return AcceptanceSuite(
            suite_id="PPA-BUSINESS",
            title="Business Acceptance",
            status="PASS",
            summary="PASS=1",
            checks=[AcceptanceCheck("BUS-001", "employee search", "PASS", "ok", critical=True)],
            critical=True,
        )

    async def fake_memory_suite(self):
        from modules.ai_console.acceptance_runner import AcceptanceCheck, AcceptanceSuite
        return AcceptanceSuite(
            suite_id="PPA-MEMORY",
            title="Memory Acceptance",
            status="SKIP",
            summary="SKIP=1",
            checks=[AcceptanceCheck("MEM-001", "memory", "SKIP", "skipped")],
        )

    async def fake_provider_suite(self):
        from modules.ai_console.acceptance_runner import AcceptanceCheck, AcceptanceSuite
        return AcceptanceSuite(
            suite_id="PPA-PROVIDER",
            title="Provider Acceptance",
            status="PASS",
            summary="PASS=1",
            checks=[AcceptanceCheck("PROV-ollama", "ollama", "PASS", "ok", critical=True)],
            critical=True,
        )

    async def fake_ui_suite(self):
        from modules.ai_console.acceptance_runner import AcceptanceCheck, AcceptanceSuite
        return AcceptanceSuite(
            suite_id="PPA-UI",
            title="UI Acceptance",
            status="PASS",
            summary="PASS=1",
            checks=[AcceptanceCheck("UI-voice", "voice", "PASS", "ok")],
        )

    async def fake_regression_suite(self):
        from modules.ai_console.acceptance_runner import AcceptanceCheck, AcceptanceSuite
        return AcceptanceSuite(
            suite_id="PPA-REGRESSION",
            title="Regression Safety Acceptance",
            status="PASS",
            summary="PASS=1",
            checks=[AcceptanceCheck("REG-health", "health", "PASS", "ok", critical=True)],
            critical=True,
        )

    async def fake_ai_quality_suite(self):
        from modules.ai_console.acceptance_runner import AcceptanceCheck, AcceptanceSuite
        return AcceptanceSuite(
            suite_id="PPA-AI-QUALITY",
            title="AI Quality Acceptance",
            status="PARTIAL PASS",
            summary="WARN=1",
            checks=[AcceptanceCheck("AI-hallucination", "hallucination", "WARN", "manual review required")],
        )

    monkeypatch.setattr(PreProductionAcceptanceRunner, "run_runtime_suite", fake_runtime_suite)
    monkeypatch.setattr(PreProductionAcceptanceRunner, "run_business_suite", fake_business_suite)
    monkeypatch.setattr(PreProductionAcceptanceRunner, "run_memory_suite", fake_memory_suite)
    monkeypatch.setattr(PreProductionAcceptanceRunner, "run_provider_suite", fake_provider_suite)
    monkeypatch.setattr(PreProductionAcceptanceRunner, "run_ui_suite", fake_ui_suite)
    monkeypatch.setattr(PreProductionAcceptanceRunner, "run_regression_suite", fake_regression_suite)
    monkeypatch.setattr(PreProductionAcceptanceRunner, "run_ai_quality_suite", fake_ai_quality_suite)

    report_path = tmp_path / "PRE_PRODUCTION_ACCEPTANCE_REPORT.md"
    json_path = tmp_path / "pre_production_acceptance_report.json"
    runner = PreProductionAcceptanceRunner(report_path=report_path, json_path=json_path)

    report = await runner.run()

    assert report.verdict == "PARTIAL PASS"
    assert report_path.exists()
    assert json_path.exists()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["verdict"] == "PARTIAL PASS"
    assert any(item["suite_id"] == "PPA-RUNTIME" for item in data["suites"])
    assert "AI Quality Acceptance" in report_path.read_text(encoding="utf-8")
