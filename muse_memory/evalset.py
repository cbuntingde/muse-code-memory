"""Golden evaluation set for recall and capture quality.

A small, frozen, labelled corpus so ranking or extractor changes report
measured precision/recall instead of vibes. `run_eval` is exercised by
tests/test_eval_golden.py and by `python3 -m muse_memory eval`.
"""

from __future__ import annotations

from . import extractor, rag
from .store import MemoryRecord

_RECORDS = [
    ("deploy-process.md", "Deploy process",
     "# Deploy process\n\n- Deploy with blue-green slots on Fridays after tests pass.\n", 4),
    ("database-choice.md", "Database choice",
     "# Database choice\n\n- We decided to use Postgres for session storage.\n", 4),
    ("test-rule.md", "Test rule",
     "# Test rule\n\n- Always run the full test suite before merging code.\n", 5),
    ("editor-preference.md", "Editor preference",
     "# Editor preference\n\n- I prefer vim keybindings and two-space indent.\n", 3),
    ("pasta-recipe.md", "Pasta recipe",
     "# Pasta recipe\n\n- Boil pasta water with plenty of salt first.\n", 2),
    ("release-checklist.md", "Release checklist",
     "# Release checklist\n\n- Tag the release, build the wheel, publish notes.\n", 3),
]

_QUERIES = [
    ("how do we deploy on Friday", "deploy-process.md"),
    ("which database did we choose for sessions", "database-choice.md"),
    ("what tests before merging", "test-rule.md"),
    ("editor keybindings indent preference", "editor-preference.md"),
    ("how to cook pasta", "pasta-recipe.md"),
    ("release tag wheel publish", "release-checklist.md"),
]

_CAPTURE = [
    ("We decided to use Postgres for session storage.", True),
    ("Always run the full test suite before merging code.", True),
    ("I prefer vim keybindings in every editor I use daily.", True),
    ("Fixed the login crash; root cause was an expired token cache.", True),
    ("Thanks, that looks great, let me check the other thing now.", False),
    ("Lorem ipsum placeholder text for the mockup page layout.", False),
]


def _records() -> list[MemoryRecord]:
    return [MemoryRecord(id=f"eval:{i}", scope="project-personal", file=f,
                         title=t, content=c, importance=imp)
            for i, (f, t, c, imp) in enumerate(_RECORDS)]


def run_eval() -> dict:
    idx = rag.RagIndex()
    idx.build(_records())
    top1 = at3 = 0
    details = []
    for query, expected in _QUERIES:
        hits = idx.search(query, top_k=3)
        files = [h.file for h in hits]
        if files and files[0] == expected:
            top1 += 1
        if expected in files:
            at3 += 1
        details.append({"query": query, "expected": expected, "got": files})
    tp = fp = fn = 0
    for text, expected in _CAPTURE:
        got = bool(extractor.extract_candidates(text, source="eval"))
        if got and expected:
            tp += 1
        elif got and not expected:
            fp += 1
        elif not got and expected:
            fn += 1
    n = len(_QUERIES)
    return {
        "recall_top1": top1 / n if n else 0.0,
        "recall_at3": at3 / n if n else 0.0,
        "capture_precision": tp / (tp + fp) if (tp + fp) else 1.0,
        "capture_recall": tp / (tp + fn) if (tp + fn) else 1.0,
        "queries": details,
        "capture_counts": {"tp": tp, "fp": fp, "fn": fn},
    }
