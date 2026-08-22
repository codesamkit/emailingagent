"""No-reply classification entry point — matches the frozen signature in
interfaces/README.md."""

from __future__ import annotations

from classification.llm_fallback import classify_ambiguous
from classification.rules import apply_rules
from models.schema import RawEmail


def is_no_reply(email: RawEmail) -> tuple[bool, str]:
    """Returns (is_no_reply, reason). Runs the rule-based pass first;
    falls back to an LLM call only when rules are inconclusive."""

    rule_result = apply_rules(email)
    if rule_result is not None:
        return rule_result
    return classify_ambiguous(email)
