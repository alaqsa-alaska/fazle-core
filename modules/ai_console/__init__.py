"""Phase 2C registry-driven skeleton for the Fazle AI Operating Console."""

from modules.ai_console.acceptance_runner import PreProductionAcceptanceRunner
from modules.ai_console.orchestrator import (
    CANONICAL_RUNTIME_INVENTORY_PATH,
    ConsoleOrchestrationResponse,
    MockOrchestrationResponse,
    classify_message,
    orchestrate_console_request,
    orchestrate_mock_request,
)
from modules.ai_console.registry import (
    RegistryEntry,
    build_registry_loader,
    load_all_registry_entries,
)

__all__ = [
    "CANONICAL_RUNTIME_INVENTORY_PATH",
    "ConsoleOrchestrationResponse",
    "MockOrchestrationResponse",
    "PreProductionAcceptanceRunner",
    "RegistryEntry",
    "build_registry_loader",
    "classify_message",
    "load_all_registry_entries",
    "orchestrate_console_request",
    "orchestrate_mock_request",
]
