"""The two failure shapes a stage adapter may raise, and their common base.

These live outside :mod:`periplus.orchestrator.hermes` because both sides of the stage
contract need them: Hermes catches them, and the adapters in
:mod:`periplus.orchestrator.stages` raise them. Hermes already imports ``stages``, so
``stages`` cannot import ``hermes`` back. :mod:`periplus.orchestrator.hermes` re-exports
everything here, so ``from periplus.orchestrator.hermes import TransientStageError`` keeps
working exactly as it did.
"""

from __future__ import annotations

__all__ = ["HermesError", "StageFailure", "TransientStageError"]


class HermesError(Exception):
    """Base for every orchestration-level failure."""


class TransientStageError(HermesError):
    """A stage adapter's retryable failure: worth attempting again with the same input."""


class StageFailure(HermesError):
    """A stage adapter's non-retryable failure: the same input would fail the same way."""
