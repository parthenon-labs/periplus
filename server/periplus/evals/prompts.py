"""Which prompt produced these numbers.

A metric movement is only evidence if it can be attributed. The obvious candidate for that
attribution — :attr:`~periplus.models.ModelCall.prompt_hash` — cannot do the job across
runs, and it is worth being precise about why: it digests the *whole* assembled prompt,
which for research includes the brief's generated id and for verification includes freshly
minted claim and evidence UUIDs. Two identical runs therefore produce different digests. It
is a fine key for grouping repeated calls *within* one run, which is what it was built for,
and useless as a prompt version.

So this module digests the stage system prompts instead — the text that actually changes
when someone edits a prompt. It reaches into the agent modules' private constants on
purpose: that is the stopgap, and the discomfort is the signal. The fix is to lift those
constants into a versioned prompt module and have this read the version directly; until
then a digest of the current text is honest and cheap, and the eval report gains the one
column that makes a before/after diff defensible.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from periplus.agents import research, verification
from periplus.models import Stage

__all__ = ["STAGE_SYSTEM_PROMPTS", "system_prompt_digests"]

#: Stage to its system prompt text. Only the stages the offline harness drives; adding a
#: stage here without wiring it into the harness would report a digest for a prompt no case
#: ever exercised.
STAGE_SYSTEM_PROMPTS: dict[Stage, str] = {
    Stage.RESEARCH: research._SYSTEM_PROMPT,
    Stage.VERIFY: verification._SYSTEM_PROMPT,
}


def system_prompt_digests(stages: Iterable[Stage]) -> dict[str, str]:
    """Short, stable digests of the system prompts ``stages`` will use.

    Unknown to this module means reported as ``"unknown"`` rather than omitted: a silently
    missing digest in a report reads as "the prompt did not change".
    """
    digests: dict[str, str] = {}
    for stage in stages:
        text = STAGE_SYSTEM_PROMPTS.get(stage)
        if text is None:
            digests[stage.value] = "unknown"
            continue
        digests[stage.value] = hashlib.sha256(text.strip().encode()).hexdigest()[:12]
    return digests
