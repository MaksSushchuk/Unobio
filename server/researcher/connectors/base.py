"""Connector contract."""

from __future__ import annotations

from typing import Protocol

from researcher.schema import Evidence, SourceStatus, Subject


class Connector(Protocol):
    source: str
    # Status of the last fetch() (ok/error/records/timing); the pipeline adds it to sources_status.
    last_status: SourceStatus | None

    def fetch(self, subject: Subject) -> list[Evidence]:
        """Return evidence for the subject. Must not raise; the pipeline records failures."""
        ...
