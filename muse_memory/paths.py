"""Path resolution for Muse Code memory scopes.

Layout (identical on Windows, macOS, and Linux):
- config: $XDG_CONFIG_HOME/muse else ~/.config/muse  (settings.json, ...)
- memories base: <config>/memories/
- personal-project memory: <base>/projects/<slug>/MEMORY.md
- global (personal, machine-wide): <base>/global/MEMORY.md
- shared project memory: <repo>/.agents/memory/MEMORY.md (committed)
- diagnostics log: <base>/diagnostics.log (rotated)

RAG/meta sidecars live inside each scope dir as dotfiles, ignored by the
agent index. The pre-1.1 base (<data>/memory/, where data is
$XDG_DATA_HOME/muse else ~/.local/share/muse) is migrated automatically
by migrate_legacy_base() on first use.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

MEMORY_INDEX = "MEMORY.md"
RAG_INDEX = ".rag-index.db"
LEGACY_RAG_INDEX = ".rag-index.json"
ACCESS_FILE = ".access.jsonl"
TRASH_DIR = ".trash"
META_FILE = ".memory-meta.json"
PENDING_FILE = ".pending.jsonl"
SNAPSHOT_PREFIX = ".snapshot-"
ARCHIVE_DIR = "archive"

_SLUG_CLEAN = re.compile(r"[^A-Za-z0-9]+")
# Muse's own slug dirs are <stem>-<hex-hash> too, but the stem uses a
# different sanitizer: every non-alphanumeric character becomes its own
# dash, so "C:\work" becomes "C--work" while the collapse form is
# "C-work". The two must still be recognised as the same workspace.
_SLUG_PER_CHAR = re.compile(r"[^A-Za-z0-9]")
# Adopted slug dirs are <stem>-<hex-hash>: the suffix guard stops a
# workspace from claiming a sibling whose stem merely extends its own.
_SLUG_DIR_RE = re.compile(r"^(?P<base>.+?)-(?P<hash>[0-9a-f]{6,40})$",
                          re.IGNORECASE)
_HASH_LEN = 12

# Muse's native memory tools (read_memory / add_memory / edit_memory) use
# their own scope names; the plugin mirrors the same roots on disk.
NATIVE_SCOPE_NAMES = {
    "global": "personal",
    "project-personal": "personal_project",
    "project-shared": "project",
}


def _windows_profile_dir() -> Path | None:
    """Home directory via the Windows shell API (works with cleared env)."""
    try:
        import ctypes
    except ImportError:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        hr = ctypes.windll.shell32.SHGetFolderPathW(
            None, 0x28, None, 0, buf)  # 0x28 == CSIDL_PROFILE
    except (AttributeError, OSError, ValueError):
        return None
    if hr != 0 or not buf.value:
        return None
    candidate = Path(buf.value)
    return candidate if candidate.is_absolute() else None


def _posix_pw_home() -> Path | None:
    """Home directory via the password database (works with cleared env)."""
    try:
        import pwd
        candidate = Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (AttributeError, ImportError, KeyError, OSError):
        return None
    if str(candidate) in ("", "~") or not candidate.is_absolute():
        return None
    return candidate


def home_dir() -> Path:
    """Absolute home directory, robust against a cleared environment.

    Hooks may run without HOME/USERPROFILE set, in which case
    os.path.expanduser("~") returns the literal string "~". Returning that
    would make every caller create a "~" directory inside the project root,
    so resolve via OS fallbacks instead and raise when no home exists.
    """
    expanded = os.path.expanduser("~")
    if expanded not in ("", "~") and os.path.isabs(expanded):
        return Path(expanded)
    for key in ("USERPROFILE", "HOME"):
        val = os.environ.get(key)
        if val and os.path.isabs(val):
            return Path(val)
    drive = os.environ.get("HOMEDRIVE", "")
    hpath = os.environ.get("HOMEPATH", "")
    if drive and hpath:
        combo = drive + hpath  # e.g. "C:" + r"\Users\name", not join()
        if os.path.isabs(combo):
            return Path(combo)
    for fallback in (_windows_profile_dir, _posix_pw_home):
        try:
            candidate = fallback()
        except Exception:  # noqa: BLE001 - fallbacks must never raise
            candidate = None
        if candidate is not None:
            return candidate
    raise RuntimeError(
        "cannot determine home directory (HOME/USERPROFILE unset and OS "
        "fallbacks failed); refusing to use a relative '~' path")


def _xdg_base(var: str) -> Path | None:
    """Absolute XDG base dir, or None when unset/empty/relative."""
    base = os.environ.get(var)
    if base and os.path.isabs(base):
        return Path(base)
    return None


def muse_config_dir() -> Path:
    base = _xdg_base("XDG_CONFIG_HOME")
    if base is not None:
        return base / "muse"
    return home_dir() / ".config" / "muse"


def muse_data_dir() -> Path:
    base = _xdg_base("XDG_DATA_HOME")
    if base is not None:
        return base / "muse"
    # Windows without XDG vars: Muse uses %USERPROFILE%\.local\share\muse
    return home_dir() / ".local" / "share" / "muse"


def memories_base_dir() -> Path:
    """Base dir for plugin-managed memory: <config>/memories/.

    Same location on every OS (only the config root itself is platform
    specific), so installs behave identically on Windows, macOS, and Linux.
    """
    return muse_config_dir() / "memories"


def legacy_memory_base() -> Path:
    """Pre-1.1 base (<data>/memory/); source for one-time migration only."""
    return muse_data_dir() / "memory"


def migrate_legacy_base() -> str:
    """Move the pre-1.1 tree into memories_base_dir(); never raises.

    Merge-moves each legacy child whose name is absent at the destination,
    so nothing is ever overwritten; removes the legacy base when empty.
    Returns "moved", "merged" (partial, name collisions left behind),
    "absent" (nothing to do), or "skipped" (unexpected failure).
    Safe to call on every startup: it is a no-op once migrated.
    """
    try:
        dest = memories_base_dir()
        src = legacy_memory_base()
        if not src.is_dir():
            return "absent"
        dest.mkdir(parents=True, exist_ok=True)
        import shutil
        leftovers = 0
        for child in sorted(src.iterdir()):
            target = dest / child.name
            if target.exists():
                leftovers += 1
                continue
            try:
                shutil.move(str(child), str(target))
            except OSError:
                leftovers += 1
        try:
            if not any(src.iterdir()):
                src.rmdir()
        except OSError:
            pass
        return "merged" if leftovers else "moved"
    except Exception:  # noqa: BLE001 - migration must never break hooks
        return "skipped"


def global_memory_dir() -> Path:
    return memories_base_dir() / "global"


def projects_root() -> Path:
    return memories_base_dir() / "projects"


def canonical_cwd(cwd: str | os.PathLike | None = None) -> Path:
    p = Path(cwd) if cwd else Path.cwd()
    try:
        return p.resolve()
    except OSError:
        return p.absolute()


def native_scope_name(scope: str) -> str:
    """Muse's native scope name for a plugin scope.

    read_memory/add_memory/edit_memory take `personal`, `personal_project`
    or `project`; the plugin calls the same roots global,
    project-personal and project-shared.
    """
    return NATIVE_SCOPE_NAMES.get(scope, scope)


def _norm_stem(text: str) -> str:
    """Compare slugs across sanitizers: lower-case, one dash per run."""
    return re.sub(r"-+", "-", text.strip("-").lower())


def _stem_variants(canon: str) -> set[str]:
    """Normalized stems Muse may use for this workspace path.

    Covers both sanitizers (collapse runs, one dash per character) so a
    Windows drive letter ("C:\\work" -> "C--work") still matches the
    plugin's own "C-work".
    """
    collapse = _SLUG_CLEAN.sub("-", canon).strip("-")[:80]
    per_char = _SLUG_PER_CHAR.sub("-", canon).strip("-")[:80]
    return {_norm_stem(collapse), _norm_stem(per_char)}


def canonical_slug(cwd: str | os.PathLike | None = None) -> str:
    """The plugin's own deterministic slug for a workspace."""
    canon = str(canonical_cwd(cwd))
    stem = _SLUG_CLEAN.sub("-", canon).strip("-")
    stem = stem[:80] if len(stem) > 80 else stem
    digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()[:_HASH_LEN]
    return f"{stem}-{digest}"


def slug_for(cwd: str | os.PathLike | None = None) -> str:
    """Slug dir for a workspace, adopting a native Muse dir when present.

    The plugin's own slug is deterministic, but Muse's native memory
    tools address a workspace through their own hashed directory. When
    that directory exists the plugin adopts it, so a topic filed here is
    readable with the native read_memory tool (and the other way round).
    Adoption matches on the sanitized workspace stem, not on the hash,
    because the two implementations hash differently.
    """
    canon = str(canonical_cwd(cwd))
    own = canonical_slug(cwd)
    root = projects_root()
    if root.is_dir():
        try:
            names = sorted(p.name for p in root.iterdir() if p.is_dir())
        except OSError:
            names = []
        targets = _stem_variants(canon)
        for name in names:
            if name == own:
                continue
            match = _SLUG_DIR_RE.match(name)
            if not match:
                continue
            if _norm_stem(match.group("base")) in targets:
                return name
    return own


def personal_project_dir(cwd: str | os.PathLike | None = None) -> Path:
    return projects_root() / slug_for(cwd)


def _merge_meta(own_dir: Path, dest: Path, keys: list[str]) -> None:
    """Copy sidecar metadata for the given relative paths into dest."""
    import json
    try:
        source = json.loads((own_dir / META_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        source = {}
    if not isinstance(source, dict):
        source = {}
    try:
        current = json.loads((dest / META_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = {}
    if not isinstance(current, dict):
        current = {}
    changed = False
    for key in keys:
        if key in source and key not in current:
            current[key] = source[key]
            changed = True
    if not changed:
        return
    dest.mkdir(parents=True, exist_ok=True)
    tmp = (dest / META_FILE).with_suffix(".tmp")
    tmp.write_text(json.dumps(current, indent=1), encoding="utf-8")
    os.replace(tmp, dest / META_FILE)


def sync_native_topics(cwd: str | os.PathLike | None = None) -> str:
    """Copy plugin-authored topics into an adopted native memory dir.

    A workspace can end up with two directories: the plugin's own slug
    and Muse's native slug. The plugin then addresses the native one (so
    read_memory finds the files), but topics written earlier live in the
    plugin's directory. They are copied across - never overwriting - and
    their metadata is merged.

    Returns "copied", "same", "absent", or "failed".
    """
    own_dir = projects_root() / canonical_slug(cwd)
    dest = personal_project_dir(cwd)
    if dest == own_dir:
        return "same"
    if not own_dir.is_dir():
        return "absent"
    try:
        import shutil

        from .locking import scope_lock
        copied: list[str] = []
        with scope_lock(dest):
            for src in sorted(own_dir.rglob("*.md")):
                rel = src.relative_to(own_dir)
                if rel.parts[0].startswith(".") or rel.name == MEMORY_INDEX:
                    continue
                target = dest / rel
                if target.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
                copied.append(rel.as_posix())
            if copied:
                _merge_meta(own_dir, dest, copied)
        return "copied" if copied else "same"
    except (OSError, ValueError):
        return "failed"


def git_root(cwd: str | os.PathLike | None = None) -> Path | None:
    cur = canonical_cwd(cwd)
    while True:
        if (cur / ".git").exists():
            return cur
        parent = cur.parent
        if parent == cur:
            return None
        cur = parent


def shared_project_dir(cwd: str | os.PathLike | None = None) -> Path | None:
    """Committed repo memory: <git-root>/.agents/memory (None outside a repo)."""
    root = git_root(cwd)
    if root is None:
        return None
    return root / ".agents" / "memory"


def all_scope_dirs(cwd: str | os.PathLike | None = None) -> dict[str, Path | None]:
    return {
        "global": global_memory_dir(),
        "project-personal": personal_project_dir(cwd),
        "project-shared": shared_project_dir(cwd),
    }


def diagnostics_log() -> Path:
    """Hook diagnostics log: <base>/diagnostics.log (rotated)."""
    return memories_base_dir() / "diagnostics.log"


def user_config_file() -> Path:
    return muse_config_dir() / "memory" / "config.json"


def project_config_file(cwd: str | os.PathLike | None = None) -> Path | None:
    root = git_root(cwd)
    if root is None:
        return None
    return root / ".muse" / "memory.json"
