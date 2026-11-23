"""What the browser may choose from: ids, titles and descriptions only (no file names, no hashes, no paths).

Kept free of any pipeline import so the browser-facing side needs neither CrewAI nor pandas. A test asserts that
this list always matches the worker's pinned registry (live.scenarios.DEFAULT_SCENARIOS).
"""
from __future__ import annotations

from typing import NamedTuple


class PublicScenario(NamedTuple):
    id: str
    title: str
    description: str


PUBLIC_SCENARIOS = (
    PublicScenario("pass", "PASS: original dataset", "The original, unmodified public dataset workbook."),
    PublicScenario("fail", "FAIL: deliberately corrupted workbook",
                   "A local test copy with one SatisfactionScore set to 9 (approved range 1-5)."),
)
