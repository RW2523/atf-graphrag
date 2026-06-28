"""Guardrail providers — content safety / PII control over LLM I/O.

Selected by config (guardrails.provider, default "none"). The contract is two
methods that take text and return a GuardrailResult; a guardrail may pass text
through unchanged, redact parts of it, or block it entirely.

  filter_input(text, source="user")  -> GuardrailResult   # prompts / context
  filter_output(text)                -> GuardrailResult   # model answers

LocalGuardrail is a dependency-free default: when enabled it applies regex PII
redaction (SSN / credit-card / email / phone) and a denied-term blocklist, so
even the local/OSS profile gets a baseline. BedrockGuardrail (in bedrock.py)
delegates to Amazon Bedrock Guardrails for managed policies (denied topics,
content filters, contextual grounding, PII). Swapping is a config change only.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

# A GuardrailResult is a plain dict so it crosses module boundaries with no
# import coupling: {"text": str, "blocked": bool, "action": str, "reasons": []}.
BLOCKED_MESSAGE = ("[blocked by guardrail] This response was withheld because it "
                   "violated a configured content policy.")


def _result(text: str, blocked: bool = False, reasons: Optional[List[str]] = None,
            action: str = "NONE") -> Dict:
    return {"text": text, "blocked": blocked, "action": action,
            "reasons": reasons or []}


class Guardrail:
    name = "none"
    enabled = False

    def filter_input(self, text: str, source: str = "user") -> Dict:
        return _result(text)

    def filter_output(self, text: str) -> Dict:
        return _result(text)


class LocalGuardrail(Guardrail):
    """Offline guardrail: regex PII redaction + denied-term blocklist. Disabled
    by default (pass-through) so it never changes behaviour unless turned on."""
    name = "local"

    _PII = [
        ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
        ("CREDIT_CARD", re.compile(r"\b(?:\d[ -]*?){13,16}\b")),
        ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
        ("PHONE", re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")),
    ]

    def __init__(self, cfg: Optional[Dict] = None):
        cfg = cfg or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.redact_pii = bool(cfg.get("redact_pii", True))
        self.denied_terms = [t.lower() for t in cfg.get("denied_terms", [])]

    def _scan(self, text: str) -> Dict:
        if not self.enabled or not text:
            return _result(text)
        reasons: List[str] = []
        out = text
        if self.redact_pii:
            for label, rx in self._PII:
                if rx.search(out):
                    out = rx.sub(f"[REDACTED-{label}]", out)
                    reasons.append(f"pii:{label}")
        low = out.lower()
        for term in self.denied_terms:
            if term and term in low:
                return _result(BLOCKED_MESSAGE, blocked=True,
                               reasons=reasons + [f"denied_term:{term}"],
                               action="GUARDRAIL_INTERVENED")
        return _result(out, blocked=False, reasons=reasons,
                       action="GUARDRAIL_INTERVENED" if reasons else "NONE")

    def filter_input(self, text: str, source: str = "user") -> Dict:
        return self._scan(text)

    def filter_output(self, text: str) -> Dict:
        return self._scan(text)
