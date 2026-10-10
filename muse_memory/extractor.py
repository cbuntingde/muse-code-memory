"""Heuristic extraction of memory candidates from session text.

Hooks observe tool results and prompts; this module turns raw text into
structured candidates (topic, importance, tags, scope hint) without any
model call, so it works offline inside hook context. Precision is favored
over recall: only explicit decisions, preferences, standing rules, fixes,
and durable facts are captured. Chatty or task-local content is ignored.
"""

from __future__ import annotations

import re

# (pattern, kind, base_importance, tags)
RULE_PATTERNS: list[tuple[re.Pattern, str, int, list[str]]] = [
    (re.compile(r"\b(always|never|must|must not|don't|do not)\b.{0,120}", re.I),
     "rule", 4, ["rule"]),
    (re.compile(r"\b(decided|decision|we will|we'll|going forward|from now on)\b.{0,160}", re.I),
     "decision", 4, ["decision"]),
    (re.compile(r"\b(prefer|preference|like to|my style|convention)\b.{0,160}", re.I),
     "preference", 3, ["preference"]),
    (re.compile(r"\b(remember|note that|keep in mind|important:?)\b.{0,160}", re.I),
     "note", 3, ["note"]),
    (re.compile(r"\b(fixed|fix was|root cause|workaround|solution was)\b.{0,160}", re.I),
     "fix", 3, ["fix"]),
    (re.compile(r"\b(error|failed|failure|broken|crash)\b.{0,80}(because|due to|caused by).{0,120}", re.I),
     "gotcha", 3, ["gotcha"]),
    (re.compile(r"\b(uses?|runs? on|requires?|depends on)\s+[A-Z][\w.+-]*(?:\s+\d[\w.]*)?", re.I),
     "fact", 2, ["fact"]),
]

SKIP_PATTERNS = re.compile(
    r"(lorem|placeholder|todo:?($|\s)|fixme|xxx|asdf|test test|hello world"
    r"|thank you|thanks|sure,|okay,? (here|let)|let me )", re.I)

CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
PROJECT_HINTS = re.compile(
    r"(\.py|\.js|\.ts|\.md|\.json|\.toml|\.yaml|\byaml\b|repo\b|branch\b|"
    r"endpoint|migration|schema|deploy|pipeline|test suite|package\.json|"
    r"requirements\.txt|pyproject|dockerfile|makefile)", re.I)
GLOBAL_HINTS = re.compile(
    r"\b(i (prefer|like|always|never)|my (style|preference|workflow|editor|"
    r"shell|os)|across all projects|in general)\b", re.I)

MAX_CANDIDATE_CHARS = 600
MIN_CANDIDATE_WORDS = 5

# (kind, pattern, match-group to redact or None for the whole match).
# Group redaction keeps the surrounding fact ("password = [redacted]")
# while whole-match redaction removes opaque blobs (bare keys, JWTs).
SECRET_PATTERNS: list[tuple[str, re.Pattern, int | None]] = [
    ("aws-key", re.compile(r"AKIA[0-9A-Z]{16}"), None),
    ("github-token", re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|"
                               r"github_pat_[A-Za-z0-9_]{20,}"), None),
    ("slack-token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), None),
    ("slack-webhook", re.compile(
        r"https://hooks\.slack\.com/services/[A-Za-z0-9/_-]+"), None),
    ("stripe-key", re.compile(r"s[kr]_live_[A-Za-z0-9]{10,}"), None),
    ("bearer-token", re.compile(r"[Bb]earer\s+[A-Za-z0-9\-._~+/]{20,}=*"),
     None),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."
                       r"[A-Za-z0-9_-]{8,}"), None),
    ("private-key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"), None),
    ("secret-assignment", re.compile(
        r"(?i)\b(api[_-]?key|secret|client-secret|passwd|password|"
        r"auth[_-]?token|access[_-]?token)\s*[:=]\s*['\"]?"
        r"([^\s'\";,]+)"), 2),
    ("url-password", re.compile(
        r"(?i)\b([a-z][a-z0-9+.-]*://[^/\s:@]+:)([^/\s@?#]+)(@)"), 2),
]

# Sentences dominated by these are pasted credentials, not durable facts,
# so the extractor drops them; other matches redact the value and keep
# the sentence.
SECRET_DROP_KINDS = frozenset({
    "aws-key", "github-token", "slack-token", "slack-webhook", "stripe-key",
    "bearer-token", "jwt", "private-key",
})


def _sub_group(pattern: re.Pattern, text: str, group: int,
               marker: str) -> tuple[str, int]:
    out: list[str] = []
    last = 0
    count = 0
    for m in pattern.finditer(text):
        out.append(text[last:m.start(group)])
        out.append(marker)
        last = m.end(group)
        count += 1
    out.append(text[last:])
    return "".join(out), count


def redact_secrets(text: str) -> tuple[str, list[str]]:
    """Replace credential material with `[redacted:<kind>]` markers.

    Returns (cleaned_text, sorted_kinds_found). Bare security words
    ("token", "secret", "password" without a value) are left alone, and
    running on already-redacted text is a no-op.
    """
    if not text:
        return text, []
    kinds: list[str] = []
    cleaned = text
    for kind, pattern, group in SECRET_PATTERNS:
        marker = f"[redacted:{kind}]"
        if group is None:
            cleaned, count = pattern.subn(marker, cleaned)
        else:
            cleaned, count = _sub_group(pattern, cleaned, group, marker)
        if count:
            kinds.append(kind)
    return cleaned, sorted(kinds)


def _clean(text: str) -> str:
    text = CODE_FENCE_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9*-])", text)
    out = []
    for part in parts:
        part = part.strip().lstrip("-* ").strip()
        if len(part.split()) >= MIN_CANDIDATE_WORDS and len(part) >= 30:
            out.append(part[:MAX_CANDIDATE_CHARS])
    return out


def suggest_topic(text: str, kind: str) -> str:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9+-]*", text.lower())
    stop = {"the", "a", "an", "and", "or", "for", "with", "that", "this",
            "from", "they", "have", "will", "was", "were", "are", "our",
            "your", "you", "use", "using", "when", "then", "than", "into",
            "which", "their", "there", "should", "would", "about", "also"}
    keywords = [w for w in words if w not in stop and len(w) > 2][:3]
    base = "-".join(keywords) if keywords else kind
    return f"{kind}-{base}"[:60]


def suggest_scope(text: str, default: str = "project-personal") -> str:
    if GLOBAL_HINTS.search(text):
        return "global"
    if PROJECT_HINTS.search(text):
        return "project-personal"
    return default


def extract_candidates(text: str, source: str = "tool",
                       session_id: str = "",
                       default_scope: str = "project-personal",
                       max_candidates: int = 5) -> list[dict]:
    """Return candidate dicts: text, kind, topic, importance, tags, scope, source."""
    if not text or len(text.strip()) < 30:
        return []
    cleaned = _clean(text[:20000])
    if not cleaned:
        return []
    seen: set[str] = set()
    candidates: list[dict] = []
    for sentence in _sentences(cleaned):
        if SKIP_PATTERNS.search(sentence):
            continue
        sentence, secret_kinds = redact_secrets(sentence)
        if not SECRET_DROP_KINDS.isdisjoint(secret_kinds):
            continue  # pasted credential, not a durable fact
        for pattern, kind, importance, tags in RULE_PATTERNS:
            m = pattern.search(sentence)
            if not m:
                continue
            key = sentence.lower()
            if key in seen:
                break
            seen.add(key)
            importance_adj = importance
            if source == "tool-failure":
                importance_adj = min(5, importance + 1)
            candidates.append({
                "text": sentence,
                "kind": kind,
                "topic": suggest_topic(sentence, kind),
                "importance": importance_adj,
                "tags": list(tags),
                "scope": suggest_scope(sentence, default_scope),
                "source": source,
                "session_id": session_id,
            })
            break  # one rule per sentence
        if len(candidates) >= max_candidates:
            break
    return candidates


def candidate_to_bullet(candidate: dict) -> str:
    tags = " ".join(f"#{t}" for t in candidate.get("tags", []))
    suffix = f" {tags}" if tags else ""
    return f"- {candidate['text'].strip()}{suffix}"
