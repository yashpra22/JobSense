"""URL-based ATS adapter registry.

The registry is intentionally dependency-light so the large career watcher can
adopt adapters incrementally without changing its public behavior in one step.
"""
from .base import ATSAdapter


class AdapterRegistry:
    def __init__(self, adapters: list[ATSAdapter] | None = None):
        self.adapters = list(adapters or [])

    def register(self, adapter: ATSAdapter) -> None:
        self.adapters.append(adapter)

    def resolve(self, url: str) -> ATSAdapter | None:
        for adapter in self.adapters:
            if adapter.matches(url):
                return adapter
        return None
