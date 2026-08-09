"""Common interface for ATS-specific job discovery adapters."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    title: str
    company: str
    location: str
    url: str
    portal: str
    description: Optional[str] = None


class ATSAdapter(ABC):
    name = "unknown"

    @abstractmethod
    def matches(self, url: str) -> bool:
        """Return whether this adapter owns the supplied career URL."""

    @abstractmethod
    def discover(self, url: str, company: str) -> list[JobRecord]:
        """Discover normalized jobs from the ATS endpoint."""
