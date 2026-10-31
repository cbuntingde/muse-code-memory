"""Project entity memory: profile, command ledger, and file registry.

Prose mining drops the facts developers actually re-ask for (which files
matter, how to build, what this project is), so these are stored as
first-class entities: three pinned topics with machine-readable bullets
that recall, injection, and maintenance all treat uniformly.

Storage reuses plain topics (no new sidecars): the ledger and registry
grow by idempotent line upserts, the profile is rewritten only when the
workspace signature changes. Pinned topics skip merge/expire/cap
retirement; the standard trim bound still caps runaway growth.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

from . import extractor
from .store import ScopeStore, iso_date, split_frontmatter, utc_now

ENTITY_KINDS = ("project-profile", "project-command", "project-file")

TOPICS: dict[str, dict] = {
    "project-profile": {
        "filename": "project-profile.md",
        "title": "Project Profile",
        "tags": ["project", "profile"],
        "importance": 4,
    },
    "project-command": {
        "filename": "command-ledger.md",
        "title": "Command Ledger",
        "tags": ["project", "build"],
        "importance": 4,
    },
    "project-file": {
        "filename": "file-registry.md",
        "title": "File Registry",
        "tags": ["project", "change"],
        "importance": 4,
    },
}

MANAGED_META = "entities_managed"


def is_entity_kind(kind: object) -> bool:
    return kind in ENTITY_KINDS


# -- commands ---------------------------------------------------------------

_NPM_RUN_FOLD = re.compile(r"\bnpm\s+run\s+(test|start|stop|restart)\b", re.I)

# Verbatim-worthy invocations: build/test/run/version commands. Applied to
# the raw command (not prose), so everyday verbs cannot match chatter.
_COMMAND_RE = re.compile(
    r"(python3?(?:\.exe)?\s+-m\s+(?:unittest|pytest|build)"
    r"|unittest\s+discover|\bpytest\b"
    r"|npm\s+(?:run\s+)?(?:build|test|start|ci)\b|npx\s+\S+"
    r"|cargo\s+(?:build|test|check|run|clippy)\b"
    r"|go\s+(?:build|test|run|vet)\b"
    r"|dotnet\s+(?:build|test|run)\b"
    r"|gradle(?:w)?\s+(?:build|test)\b"
    r"|make\s+(?:build|test|check|all|clean|install)\b"
    r"|\b(python3?|python|node|npm|cargo|rustc|go|java|dotnet|powershell)"
    r"(\s+--version|\s+-V|\s+version)\b)", re.I)


def canonical_command(command: str) -> str:
    """Collapse whitespace and fold `npm run test` to `npm test`."""
    collapsed = " ".join((command or "").split())[:300]
    return _NPM_RUN_FOLD.sub(r"npm \1", collapsed)


def is_noteworthy_command(command: str) -> bool:
    return bool(command and _COMMAND_RE.search(canonical_command(command)))


def command_line(command: str, seen: str = "") -> str:
    stamp = seen or iso_date(utc_now())
    return f"- [cmd] `{canonical_command(command)}` (seen: {stamp})"


# -- files ------------------------------------------------------------------

_PATH_TOKEN_RE = re.compile(r"[A-Za-z0-9_][\w./\\-]*\.\w{1,8}\b")
_CODE_EXTS = frozenset({
    "py", "js", "ts", "tsx", "jsx", "mjs", "cjs", "md", "mdx", "json",
    "toml", "yaml", "yml", "cfg", "ini", "rs", "go", "java", "rb", "php",
    "sh", "ps1", "css", "scss", "html", "sql", "xml", "gradle", "lock",
})


def _clean_token(token: str) -> str:
    return token.strip().strip("`'\"()[],.;:").strip()


def extract_file_mentions(text: str, max_items: int = 5) -> list[tuple[str, str]]:
    """(path, sentence) pairs for project files named in prose.

    A mention only counts with project context (a repo-relative path or a
    project hint in the sentence), so everyday chatter never files. Machine
    artifacts are rejected with the same guard as prose capture.
    """
    if not text or extractor.fragment_reason(text):
        return []
    out: list[tuple[str, str]] = []
    for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\-*`])", text):
        sentence = re.sub(r"\s+", " ", sentence).strip()
        if len(sentence.split()) < 4 or len(sentence) < 20:
            continue
        if extractor.fragment_reason(sentence):
            continue
        for raw in _PATH_TOKEN_RE.findall(sentence):
            token = _clean_token(raw)
            if not token or "://" in token or token.startswith((".", "/")):
                continue
            if token.lower().endswith(".git"):
                continue  # remote URL, not a workspace file
            ext = token.rsplit(".", 1)[-1].lower()
            repo_relative = "/" in token
            if ext not in _CODE_EXTS and not repo_relative:
                continue
            if not repo_relative and not extractor.PROJECT_HINTS.search(sentence):
                continue
            if any(path == token for path, _note in out):
                continue
            out.append((token, sentence[:200]))
            if len(out) >= max_items:
                return out
    return out


def relative_path(path: str, cwd: str | os.PathLike | None) -> str:
    """Workspace-relative posix path when inside the workspace, else as-given."""
    cleaned = _clean_token(path or "")
    if not cleaned:
        return ""
    if cwd is None:
        return cleaned
    absolute = cleaned
    if not os.path.isabs(absolute):
        # Bare relative mentions resolve against the workspace, not the
        # hook's process directory.
        absolute = os.path.normpath(os.path.join(str(cwd), cleaned))
    try:
        rel = os.path.relpath(absolute, str(cwd))
    except (OSError, ValueError):
        return cleaned
    if rel.startswith(".."):
        return absolute
    return Path(rel).as_posix()


def file_line(path: str, note: str, seen: str = "") -> str:
    stamp = seen or iso_date(utc_now())
    suffix = f" — {note[:140]}" if note.strip() else ""
    return f"- [file] `{path}`{suffix} (seen: {stamp})"


# -- profile scan ------------------------------------------------------------

MANIFESTS = ("pyproject.toml", "package.json", "Cargo.toml", "Cargo.lock",
             "go.mod", "Makefile", "requirements.txt", "pom.xml",
             "build.gradle", "CMakeLists.txt", "package-lock.json")

_STACK_BY_MANIFEST = {
    "pyproject.toml": "Python", "requirements.txt": "Python",
    "package.json": "Node", "package-lock.json": "Node",
    "Cargo.toml": "Rust", "Cargo.lock": "Rust",
    "go.mod": "Go", "pom.xml": "Java", "build.gradle": "Java",
    "Makefile": "Make", "CMakeLists.txt": "C/C++",
}

_REMOTE_RE = re.compile(r'\[remote\s+"origin"\][^\[]*?url\s*=\s*(\S+)',
                        re.I | re.S)
_HEADING_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


def _read_head(path: Path, limit: int = 4096) -> str:
    try:
        with path.open("rb") as fh:
            return fh.read(limit).decode("utf-8", "replace")
    except OSError:
        return ""


def _git_remote(cwd: Path) -> str:
    config = _read_head(cwd / ".git" / "config")
    match = _REMOTE_RE.search(config)
    return match.group(1).strip() if match else ""


def scan_profile(cwd: str | os.PathLike | None) -> list[str]:
    """Bounded workspace scan: manifests, README heading, git remote."""
    if not cwd:
        return []
    root = Path(str(cwd))
    bullets = [f"- project: {root.name}"]
    present = [name for name in MANIFESTS if (root / name).is_file()]
    stacks = sorted({_STACK_BY_MANIFEST[name]
                     for name in present if name in _STACK_BY_MANIFEST})
    if stacks:
        bullets.append(f"- stack: {', '.join(stacks)}")
    if present:
        bullets.append(f"- manifests: {', '.join(present)}")
    readme = ""
    for name in ("README.md", "readme.md", "README.txt"):
        readme = _read_head(root / name)
        if readme.strip():
            break
    heading = _HEADING_RE.search(readme)
    if heading:
        bullets.append(f"- docs: README — {heading.group(1).strip()[:100]}")
    elif readme.strip():
        first = next((ln.strip() for ln in readme.splitlines() if ln.strip()),
                     "")
        if first:
            bullets.append(f"- docs: README — {first[:100]}")
    remote = _git_remote(root)
    if remote:
        bullets.append(f"- remote: {remote}")
    return bullets[:8]


def _profile_signature(cwd: str | os.PathLike | None) -> str:
    return "\n".join(scan_profile(cwd))


# -- topic management --------------------------------------------------------

def _entity_meta(kind: str) -> dict:
    spec = TOPICS[kind]
    return {"tags": list(spec["tags"]), "importance": spec["importance"],
            "source": "auto", "pinned": True, MANAGED_META: True}


def _read_bullets(scope: ScopeStore, filename: str) -> list[str]:
    target = scope.dir / filename
    try:
        text = target.read_text(encoding="utf-8") if target.is_file() else ""
    except OSError:
        return []
    _meta, body = split_frontmatter(text)
    return [ln.strip() for ln in body.splitlines()
            if ln.strip().startswith(("- ", "* "))]


def _store_bullets(scope: ScopeStore, kind: str, bullets: list[str]) -> None:
    spec = TOPICS[kind]
    body = f"# {spec['title']}\n\n" + "\n".join(bullets) + "\n"
    scope.write_topic(spec["title"], body, meta=_entity_meta(kind))


def _upsert_line(scope: ScopeStore, kind: str, key: str, line: str) -> dict:
    """Replace the line starting with key, or append; never duplicate."""
    spec = TOPICS[kind]
    filename = spec["filename"]
    existed = (scope.dir / filename).is_file()
    norm_key = re.sub(r"\s+", " ", key.strip().lower())
    norm_line = re.sub(r"\s+", " ", line.strip().lower())
    current = _read_bullets(scope, filename)
    if any(re.sub(r"\s+", " ", ln.lower()) == norm_line for ln in current):
        return {"action": "duplicate", "file": filename, "scope": scope.scope}
    fresh = [ln for ln in current
             if not re.sub(r"\s+", " ", ln.lower()).startswith(norm_key)]
    # Newest first: the size trim and the injection bound both favor the
    # head of the file, so recent entries must lead, not trail.
    fresh.insert(0, line)
    _store_bullets(scope, kind, fresh)
    return {"action": "appended" if existed else "created",
            "file": filename, "scope": scope.scope}


def needs_profile_sync(scope: ScopeStore,
                       cwd: str | os.PathLike | None) -> bool:
    """True when a profile scan should be queued: missing, or stale.

    A managed topic whose workspace signature changed (new manifests,
    docs, or remote) needs a refresh; anyone else's topic is never
    touched.
    """
    if not scan_profile(cwd):
        return False
    filename = TOPICS["project-profile"]["filename"]
    if not (scope.dir / filename).is_file():
        return True
    meta = scope.file_meta(filename)
    if not meta.get(MANAGED_META):
        return False
    return meta.get("profile_signature") != _profile_signature(cwd)


def ensure_profile(scope: ScopeStore,
                   cwd: str | os.PathLike | None) -> dict:
    """Create the profile topic, or refresh it when the workspace changed."""
    spec = TOPICS["project-profile"]
    filename = spec["filename"]
    target = scope.dir / filename
    if target.is_file() and not scope.file_meta(filename).get(MANAGED_META):
        # Someone else's topic: never clobber it with a rewrite.
        return {"action": "skipped", "file": filename, "scope": scope.scope}
    signature = _profile_signature(cwd)
    if not signature.strip():
        return {"action": "skipped", "file": filename, "scope": scope.scope}
    if (target.is_file()
            and scope.file_meta(filename).get("profile_signature") == signature):
        return {"action": "duplicate", "file": filename, "scope": scope.scope}
    existed = target.is_file()
    _store_bullets(scope, "project-profile", scan_profile(cwd))
    scope.set_file_meta(filename, profile_signature=signature)
    return {"action": "appended" if existed else "created",
            "file": filename, "scope": scope.scope}


def note_command(scope: ScopeStore, command: str,
                 cwd: str | os.PathLike | None,
                 seen: str = "") -> dict | None:
    """Record a build/test/run command verbatim; None when not noteworthy."""
    if not is_noteworthy_command(command or ""):
        return None
    line = command_line(command, seen)
    key = f"- [cmd] `{canonical_command(command)}`"
    return _upsert_line(scope, "project-command", key, line)


def note_file(scope: ScopeStore, path: str, note: str = "",
              cwd: str | os.PathLike | None = None,
              seen: str = "") -> dict | None:
    """Record an important file as a workspace-relative path; None when empty."""
    rel = relative_path(path, cwd)
    if not rel:
        return None
    line = file_line(rel, note, seen)
    key = f"- [file] `{rel}`"
    return _upsert_line(scope, "project-file", key, line)


def apply_candidate(cand: dict, scope: ScopeStore) -> dict:
    """File one entity candidate into its pinned topic (shared-safe)."""
    kind = cand.get("kind")
    if kind not in ENTITY_KINDS:
        raise ValueError(f"not an entity candidate: {kind!r}")
    text = str(cand.get("text", ""))
    if scope.scope == "project-shared":
        blocked = extractor.blocked_for_shared(text)
        if blocked:
            return {"action": "blocked-secret", "file": "",
                    "scope": scope.scope, "blocked": blocked}
    cleaned, secret_kinds = extractor.redact_secrets(text)
    if not extractor.SECRET_DROP_KINDS.isdisjoint(secret_kinds):
        return {"action": "blocked-secret", "file": "",
                "scope": scope.scope, "blocked": sorted(secret_kinds)}
    cwd = cand.get("cwd")
    if kind == "project-profile":
        return ensure_profile(scope, cwd)
    if kind == "project-command":
        command, _ = extractor.redact_secrets(str(cand.get("command", "")))
        result = note_command(scope, command, cwd)
        if result is None:
            return {"action": "skipped", "file": "", "scope": scope.scope}
        return result
    path, _ = extractor.redact_secrets(str(cand.get("path", "")))
    note, _ = extractor.redact_secrets(str(cand.get("note", "")))
    result = note_file(scope, path or cleaned, note, cwd)
    if result is None:
        return {"action": "skipped", "file": "", "scope": scope.scope}
    return result


def managed_files(scope: ScopeStore) -> list[str]:
    """Filenames of the entity topics that exist in this scope."""
    return [spec["filename"] for spec in TOPICS.values()
            if (scope.dir / spec["filename"]).is_file()]


# -- injection ---------------------------------------------------------------

def _exists(cwd: str | os.PathLike | None, rel: str) -> bool:
    if not cwd:
        return True
    try:
        return bool((Path(str(cwd)) / rel).exists())
    except (OSError, ValueError):
        return True


def working_set_text(scope: ScopeStore,
                     cwd: str | os.PathLike | None,
                     max_chars: int = 1200) -> str:
    """Render the entity topics as one bounded injection block.

    Registry lines for files absent from disk carry a (missing) marker,
    computed read-only at render time so sync never mutates on speculation.
    """
    sections: list[str] = []
    profile = _read_bullets(scope, TOPICS["project-profile"]["filename"])
    if profile:
        sections.append("Profile:\n" + "\n".join(profile))
    commands = _read_bullets(scope, TOPICS["project-command"]["filename"])
    if commands:
        sections.append("Commands:\n" + "\n".join(commands))
    files = _read_bullets(scope, TOPICS["project-file"]["filename"])
    if files:
        rendered = []
        for line in files:
            match = re.search(r"(\[file\]\s+`([^`]+)`)(.*)", line)
            if match and not _exists(cwd, match.group(2)):
                line = f"{match.group(1)} (missing){match.group(3)}"
            rendered.append(line)
        sections.append("Key files:\n" + "\n".join(rendered))
    if not sections:
        return ""
    block = "Project working set:\n" + "\n".join(sections)
    if len(block) <= max_chars:
        return block
    kept: list[str] = []
    total = 0
    for line in block.splitlines():
        if total + len(line) + 1 > max_chars:
            break
        kept.append(line)
        total += len(line) + 1
    return "\n".join(kept)
