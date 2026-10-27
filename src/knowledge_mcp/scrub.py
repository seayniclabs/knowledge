"""Refuse to save text that looks like a credential.

The check reports the kind of credential it found, never the matched value.
"""

from __future__ import annotations

import re

from .errors import KnowledgeError

_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("private_key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("api_key_sk", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("slack_token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}")),
    ("gitlab_token", re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}")),
    ("resend_api_key", re.compile(r"\bre_[A-Za-z0-9]{20,}")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    ("password_in_url", re.compile(r"[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@", re.I)),
    (
        "credential_assignment",
        re.compile(
            r"(?i)\b(?:password|passwd|api[_-]?key|client[_-]?secret|access[_-]?token"
            r"|auth[_-]?token|secret)\s*[:=]\s*['\"]?[^\s'\"]{6,}"
        ),
    ),
]


def find_credential_kinds(text: str) -> list[str]:
    """Names of the credential types found in ``text``, sorted, without duplicates."""
    return sorted({name for name, pattern in _RULES if pattern.search(text)})


def check_text(text: str, field: str, module: str = "open_notebook") -> None:
    kinds = find_credential_kinds(text)
    if kinds:
        raise KnowledgeError(
            module,
            "blocked_secret",
            f"the {field} looks like it contains a credential ({', '.join(kinds)}). Nothing was saved.",
            "remove the credential from the text and try again. Notes are read back by every "
            "assistant that searches this knowledge base, so secrets must stay out of them.",
        )
