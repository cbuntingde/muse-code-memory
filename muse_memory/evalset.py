"""Golden evaluation set for recall and capture quality.

A small, frozen, labelled corpus so ranking or extractor changes report
measured precision/recall instead of vibes. `run_eval` is exercised by
tests/test_eval_golden.py and by `python3 -m muse_memory eval`.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from . import entities, extractor, rag
from .store import MemoryRecord, MemoryStore

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
    ("build-and-test.md", "Build and test",
     "# Build and test\n\n- Run the suite with python3 -m unittest discover -s tests.\n", 4),
    ("toolchain.md", "Toolchain",
     "# Toolchain\n\n- Toolchain is Python 3.14 with PowerShell 7.5.\n", 4),
]

_QUERIES = [
    ("how do we deploy on Friday", "deploy-process.md"),
    ("which database did we choose for sessions", "database-choice.md"),
    ("what tests before merging", "test-rule.md"),
    ("editor keybindings indent preference", "editor-preference.md"),
    ("how to cook pasta", "pasta-recipe.md"),
    ("release tag wheel publish", "release-checklist.md"),
    ("what command runs the tests", "build-and-test.md"),
    ("which python version do we use", "toolchain.md"),
]

_CAPTURE = [
    ("We decided to use Postgres for session storage.", True),
    ("Always run the full test suite before merging code.", True),
    ("I prefer vim keybindings in every editor I use daily.", True),
    ("Fixed the login crash; root cause was an expired token cache.", True),
    ("Thanks, that looks great, let me check the other thing now.", False),
    ("Lorem ipsum placeholder text for the mockup page layout.", False),
    ("Run the tests with python3 -m unittest discover -s tests.", True),
    ("The toolchain is Python 3.14 and PowerShell 7.5 for this repo.", True),
    ("Make dinner after the deploy freeze lifts.", False),
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


_PROJECT_QUERIES = [
    ("how do I run the test suite", "command-ledger.md"),
    ("what command runs the build", "command-ledger.md"),
    ("where is auth login", "file-registry.md"),
    ("which file documents the api", "file-registry.md"),
    ("what stack is this project", "project-profile.md"),
]

_PROJECT_CAPTURE = [
    ("The auth middleware lives in src/auth.py and handles login.", True, "file"),
    ("API details are documented in docs/api.md for integrators.", True, "file"),
    ("Run the suite with npm test before pushing.", True, "command"),
    ("I added sugar to coffee and added milk too, tasty.", False, "file"),
    ("Make dinner after the deploy freeze lifts.", False, "command"),
    ("Thanks, that looks great, let me check it later.", False, "file"),
]


def _seed_project_store(work: Path) -> MemoryStore:
    """Fixture workspace with manifests, docs, and seeded entities."""
    (work / "pyproject.toml").write_text(
        '[project]\nname = "demo"\n', encoding="utf-8")
    (work / "README.md").write_text("# Demo\n", encoding="utf-8")
    remote = work / ".git" / "config"
    remote.parent.mkdir(parents=True, exist_ok=True)
    remote.write_text('[remote "origin"]\n\turl = example/demo.git\n',
                      encoding="utf-8")
    (work / "src").mkdir(exist_ok=True)
    (work / "src" / "auth.py").write_text("# auth\n", encoding="utf-8")
    (work / "docs").mkdir(exist_ok=True)
    (work / "docs" / "api.md").write_text("# API\n", encoding="utf-8")
    store = MemoryStore(work)
    store.ensure_all()
    scope = store.get_scope("project-personal")
    assert scope is not None
    entities.ensure_profile(scope, work)
    entities.note_command(scope, "npm test", work)
    entities.note_command(scope, "python3 -m pytest", work)
    entities.note_file(scope, "src/auth.py",
                        "auth middleware handles login", work)
    entities.note_file(scope, "docs/api.md",
                        "API details for integrators", work)
    return store


def run_project_eval() -> dict:
    """Recall/capture quality for project facts, on a seeded workspace.

    Hermetic: XDG homes are pointed at a temp dir and restored, so the
    eval never touches real memory stores.
    """
    saved = {key: os.environ.get(key)
             for key in ("XDG_DATA_HOME", "XDG_CONFIG_HOME")}
    tmp = tempfile.TemporaryDirectory(prefix="muse-mem-eval-")
    try:
        os.environ["XDG_DATA_HOME"] = str(Path(tmp.name) / "data")
        os.environ["XDG_CONFIG_HOME"] = str(Path(tmp.name) / "config")
        work = Path(tmp.name) / "work"
        work.mkdir()
        store = _seed_project_store(work)
        scope = store.get_scope("project-personal")
        assert scope is not None
        idx = rag.RagIndex()
        idx.build(scope.read_all())
        top1 = at3 = 0
        details = []
        for query, expected in _PROJECT_QUERIES:
            hits = idx.search(query, top_k=3)
            files = [h.file for h in hits]
            if files and files[0] == expected:
                top1 += 1
            if expected in files:
                at3 += 1
            details.append({"query": query, "expected": expected,
                            "got": files})
        tp = fp = fn = 0
        for text, expected, flavor in _PROJECT_CAPTURE:
            if flavor == "file":
                got = bool(entities.extract_file_mentions(text))
            else:
                got = entities.is_noteworthy_command(text)
            if got and expected:
                tp += 1
            elif got and not expected:
                fp += 1
            elif not got and expected:
                fn += 1
        n = len(_PROJECT_QUERIES)
        return {
            "recall_top1": top1 / n if n else 0.0,
            "recall_at3": at3 / n if n else 0.0,
            "capture_precision": tp / (tp + fp) if (tp + fp) else 1.0,
            "capture_recall": tp / (tp + fn) if (tp + fn) else 1.0,
            "queries": details,
            "capture_counts": {"tp": tp, "fp": fp, "fn": fn},
        }
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        tmp.cleanup()
