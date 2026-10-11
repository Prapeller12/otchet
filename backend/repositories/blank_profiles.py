"""Persistence boundary for the complete, atomic blank-report set."""

from typing import Any, Protocol


class BlankProfilesRepository(Protocol):
    def create(self, definition: dict[str, Any]) -> dict[str, Any]: ...

    def start_period(self, organization_id: int) -> str | None: ...
