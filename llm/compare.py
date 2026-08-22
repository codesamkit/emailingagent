"""Run the same stage under two backends and put the results side by side.

Exists because "is a small local model good enough for this?" is an empirical
question about *these* prompts and *these* emails, not something to settle by
reputation. Run it, read the output, decide.

The scoring comparison reports spread as well as values, because the way a
small model fails at scoring is not wrong answers — it is *clustered* answers.
Fifty emails all scored 70 look individually reasonable and make the ranking
worthless, which is the entire point of the score.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from models.schema import RawEmail

from .client import get_client
from .config import ANTHROPIC, OLLAMA


@dataclass
class StageResult:
    provider: str
    model: str
    email_id: str
    value: Any = None
    error: Optional[str] = None
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class Comparison:
    stage: str
    results: Dict[str, List[StageResult]] = field(default_factory=dict)

    def providers(self) -> List[str]:
        return list(self.results)

    def failures(self, provider: str) -> int:
        return sum(1 for r in self.results[provider] if not r.ok)

    def mean_seconds(self, provider: str) -> float:
        times = [r.seconds for r in self.results[provider] if r.ok]
        return statistics.mean(times) if times else 0.0


# --- stage runners ---------------------------------------------------------
# Each returns the comparable value for one email, given a client.

def _run_classify(email: RawEmail, client: Any) -> Any:
    from classification.llm_fallback import classify_ambiguous

    return classify_ambiguous(email, client=client)


def _run_score(email: RawEmail, client: Any) -> Any:
    from scoring.score import score_importance

    score, level, justification = score_importance(email, False, client=client)
    return {"score": score, "level": level.value, "justification": justification}


def _run_summarize(email: RawEmail, client: Any) -> Any:
    from summarization.summarize import summarize

    return summarize(email, client=client)


STAGE_RUNNERS: Dict[str, Callable[[RawEmail, Any], Any]] = {
    "classify": _run_classify,
    "score": _run_score,
    "summarize": _run_summarize,
}


def run_stage(
    stage: str,
    emails: Sequence[RawEmail],
    provider: str,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> List[StageResult]:
    """Run one stage over `emails` against one backend, never raising.

    A backend that is down should produce a row of errors to compare against,
    not abort the comparison — "provider B failed on everything" is itself a
    result worth seeing next to provider A's output.
    """
    runner = STAGE_RUNNERS[stage]
    from .config import model_for

    model = model_for(stage, provider)
    out: List[StageResult] = []

    try:
        client = get_client(stage, provider=provider)
    except Exception as exc:
        return [
            StageResult(provider, model, e.email_id, error="client init: {0}".format(exc))
            for e in emails
        ]

    for index, email in enumerate(emails, start=1):
        started = time.monotonic()
        try:
            value = runner(email, client)
            out.append(
                StageResult(provider, model, email.email_id, value=value,
                            seconds=time.monotonic() - started)
            )
        except Exception as exc:
            out.append(
                StageResult(provider, model, email.email_id,
                            error="{0}: {1}".format(type(exc).__name__, exc),
                            seconds=time.monotonic() - started)
            )
        if on_progress:
            on_progress(index, len(emails))
    return out


def compare(
    stage: str,
    emails: Sequence[RawEmail],
    providers: Sequence[str] = (OLLAMA, ANTHROPIC),
    on_progress: Optional[Callable[[str, int, int], None]] = None,
) -> Comparison:
    """Run one stage under each provider over the same emails."""
    if stage not in STAGE_RUNNERS:
        raise ValueError(
            "stage must be one of {0}, got {1!r}".format(tuple(STAGE_RUNNERS), stage)
        )
    result = Comparison(stage=stage)
    for provider in providers:
        result.results[provider] = run_stage(
            stage,
            emails,
            provider,
            on_progress=(lambda d, t, p=provider: on_progress(p, d, t)) if on_progress else None,
        )
    return result


# --- diagnostics -----------------------------------------------------------

def score_spread(results: Sequence[StageResult]) -> Dict[str, Any]:
    """How well-separated a provider's scores are.

    `distinct` and `stdev` are the numbers that matter. A model that returns
    68, 70, 70, 72, 70 has produced five plausible answers and zero ranking
    signal — the failure mode that a per-email eyeball check misses.
    """
    scores = [r.value["score"] for r in results if r.ok and isinstance(r.value, dict)]
    if not scores:
        return {"count": 0}
    return {
        "count": len(scores),
        "min": min(scores),
        "max": max(scores),
        "mean": round(statistics.mean(scores), 1),
        "stdev": round(statistics.pstdev(scores), 1) if len(scores) > 1 else 0.0,
        "distinct": len(set(scores)),
        "range": round(max(scores) - min(scores), 1),
    }
