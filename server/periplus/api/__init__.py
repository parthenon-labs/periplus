"""HTTP surface over Hermes.

Thin on purpose: every artifact the pipeline produces is already an inspectable Pydantic
model, so this layer adds no shape of its own beyond a run id and a status. Import
:func:`~periplus.api.app.create_app` for production wiring, or
:class:`~periplus.api.runs.RunStore` directly to drive Hermes from something other than
HTTP (a CLI, a test).
"""

from periplus.api.runs import RunEntry, RunNotFound, RunStore
from periplus.api.schemas import RunView

__all__ = ["RunEntry", "RunNotFound", "RunStore", "RunView"]
