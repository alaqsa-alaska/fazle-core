"""Observable offline provider adapter used by Phase 3 routing tests."""
from __future__ import annotations


class OfflineProviderFake:
    """Return a per-route script and expose exact route/attempt call order."""

    def __init__(self, scripts):
        self.scripts = {route_id: list(results) for route_id, results in scripts.items()}
        self.calls = []

    async def __call__(self, route, attempt_number):
        self.calls.append((route.route_id, attempt_number))
        result = self.scripts[route.route_id].pop(0)
        if isinstance(result, BaseException):
            raise result
        return result
