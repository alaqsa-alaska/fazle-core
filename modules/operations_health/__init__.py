"""Operations Health Center checks for Fazle Core dependencies."""
from __future__ import annotations

import socket
from dataclasses import dataclass, asdict


@dataclass
class HealthItem:
    name: str
    status: str
    detail: str = ""


def tcp_check(name: str, host: str, port: int, timeout: float = 0.5) -> HealthItem:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return HealthItem(name=name, status="ok", detail=f"{host}:{port} reachable")
    except OSError as exc:
        return HealthItem(name=name, status="unreachable", detail=str(exc))


def snapshot() -> list[dict]:
    checks = [
        tcp_check("Fazle Core", "127.0.0.1", 8200),
        tcp_check("Ollama", "127.0.0.1", 11434),
        tcp_check("PostgreSQL", "127.0.0.1", 5432),
        tcp_check("Redis", "127.0.0.1", 6379),
        tcp_check("WhatsApp Bridge 1", "127.0.0.1", 8082),
        tcp_check("WhatsApp Bridge 2", "127.0.0.1", 8081),
        tcp_check("Media Processor", "127.0.0.1", 8090),
        tcp_check("LocationWhere", "127.0.0.1", 8310),
    ]
    return [asdict(item) for item in checks]


def recovery_recommendations(items: list[dict]) -> list[str]:
    recs = []
    for item in items:
        if item.get("status") != "ok":
            recs.append(f"Inspect service for {item.get('name')} and verify recent logs before restart.")
    return recs
