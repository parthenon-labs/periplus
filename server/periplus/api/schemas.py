"""Response shapes for the HTTP layer.

``TripBrief`` doubles as the request body for submitting a trip — it is already a
strict, self-validating Pydantic model with no fields that must not come from a caller,
so a second, hand-maintained request schema would only drift from it over time.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from periplus.api.runs import RunEntry
from periplus.models import Run, RunStatus


class RunView(BaseModel):
    """What a caller sees for one run: its id, current status, and the artifact Hermes
    produced once it has one. ``run`` is ``None`` until the background task finishes.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    status: RunStatus
    error: str | None = None
    run: Run | None = None

    @classmethod
    def from_entry(cls, entry: RunEntry) -> RunView:
        return cls(id=entry.run_id, status=entry.status, error=entry.error, run=entry.result)
