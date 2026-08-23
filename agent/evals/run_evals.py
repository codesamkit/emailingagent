#!/usr/bin/env python3
"""Run the Ask-tab eval set against a live Valence backend and emit a grading sheet.

Each eval opens its OWN conversation (no conversationId reuse), so one bad
answer can't poison the next. Stdlib only — no new dependencies.

Usage
-----
    # run everything against a local backend
    python -m agent.evals.run_evals

    # only the ones that don't depend on stubbed tools
    python -m agent.evals.run_evals --skip-blocked

    # one tier, or one eval
    python -m agent.evals.run_evals --tier synthesis
    python -m agent.evals.run_evals --id T5-01

    # print the sheet without calling the backend
    python -m agent.evals.run_evals --render-only

Environment
-----------
    VALENCE_API      backend base URL      (default http://127.0.0.1:8000)
    VALENCE_TOKEN    bearer token          (omit if AUTH_DISABLED)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
EVAL_FILE = HERE / "ask_evals.json"

DEFAULT_API = os.environ.get("VALENCE_API", "http://127.0.0.1:8000")
TOKEN = os.environ.get("VALENCE_TOKEN")
TIMEOUT = int(os.environ.get("VALENCE_EVAL_TIMEOUT", "180"))


# --------------------------------------------------------------------------- io


def load_evals() -> Dict[str, Any]:
    with EVAL_FILE.open() as fh:
        return json.load(fh)


def ask(api: str, message: str) -> Dict[str, Any]:
    """POST one prompt, consume the SSE stream, return the collected result.

    Returns {answer, tools, events, error, elapsed}. Never raises for a
    backend-side failure — a dead backend or a provider error comes back as
    `error` so the sheet still renders and the run continues.
    """
    payload = json.dumps({"message": message}).encode()
    request = urllib.request.Request(
        api.rstrip("/") + "/api/agent/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if TOKEN:
        request.add_header("Authorization", "Bearer {0}".format(TOKEN))

    chunks: List[str] = []
    tools: List[Dict[str, Any]] = []
    error: Optional[str] = None
    started = time.monotonic()

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    event = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue

                kind = event.get("type")
                if kind == "text_delta":
                    chunks.append(event.get("text", ""))
                elif kind == "tool_start":
                    tools.append({"tool": event.get("tool"), "input": event.get("toolInput")})
                elif kind == "error":
                    error = event.get("error", "unknown agent error")
    except urllib.error.HTTPError as exc:
        error = "HTTP {0}: {1}".format(exc.code, exc.reason)
    except urllib.error.URLError as exc:
        error = "cannot reach {0} — {1}".format(api, exc.reason)
    except TimeoutError:
        error = "timed out after {0}s".format(TIMEOUT)

    return {
        "answer": "".join(chunks).strip(),
        "tools": tools,
        "error": error,
        "elapsed": round(time.monotonic() - started, 1),
    }


# ----------------------------------------------------------------------- render


def bullets(items: List[str], marker: str = "-") -> str:
    return "\n".join("{0} {1}".format(marker, item) for item in items) if items else "_(none)_"


def render_eval(spec: Dict[str, Any], result: Optional[Dict[str, Any]]) -> str:
    out: List[str] = []
    flag = "  ⚠️ **blocked on stubbed tools**" if spec.get("blocked_on_stub") else ""
    out.append("### {0} · {1}{2}".format(spec["id"], spec["tier"], flag))
    out.append("")
    out.append("> **{0}**".format(spec["prompt"]))
    out.append("")
    out.append("_{0}_".format(spec["tests"]))
    out.append("")

    if result is not None:
        if result["error"]:
            out.append("**Answer** — ❌ `{0}`".format(result["error"]))
        else:
            answer = result["answer"] or "_(empty response)_"
            out.append("**Answer** ({0}s)".format(result["elapsed"]))
            out.append("")
            out.append("```")
            out.append(answer)
            out.append("```")
        out.append("")
        called = [t["tool"] for t in result["tools"]]
        out.append("**Tools called:** {0}".format(", ".join("`{0}`".format(c) for c in called) or "_none_"))
        out.append("**Tools expected:** {0}".format(", ".join("`{0}`".format(c) for c in spec.get("expected_tools", [])) or "_none_"))
        out.append("")

    out.append("**Must include**")
    out.append("")
    out.append(bullets(spec.get("must_include", []), "- [ ]"))
    out.append("")
    out.append("**Fail signals**")
    out.append("")
    out.append(bullets(spec.get("fail_signals", [])))
    out.append("")

    if spec.get("should_cite"):
        out.append("**Should cite:** {0}".format(" · ".join(spec["should_cite"])))
        out.append("")
    if spec.get("expect_refusal"):
        out.append("**This one should decline to answer.** Confidently answering it is a 0.")
        out.append("")
    if spec.get("notes"):
        out.append("> ℹ️ {0}".format(spec["notes"]))
        out.append("")

    out.append("**Score:** ` ` /3    **Notes:**")
    out.append("")
    out.append("---")
    out.append("")
    return "\n".join(out)


def render_sheet(data: Dict[str, Any], specs: List[Dict[str, Any]], results: Dict[str, Any], api: str) -> str:
    ran = results is not None and len(results) > 0
    head: List[str] = []
    head.append("# Valence — Ask-tab eval run" if ran else "# Valence — Ask-tab eval sheet")
    head.append("")
    head.append("Corpus: {0}".format(data["corpus"]))
    if ran:
        head.append("Backend: `{0}`".format(api))
        head.append("Run at: {0}".format(time.strftime("%Y-%m-%d %H:%M:%S")))
    head.append("")
    head.append("**Grading:** {0}".format(data["grading"]))
    head.append("")

    by_tier: Dict[str, List[Dict[str, Any]]] = {}
    for spec in specs:
        by_tier.setdefault(spec["tier"], []).append(spec)

    head.append("| Tier | Evals | What it probes |")
    head.append("| --- | --- | --- |")
    descriptions = {
        "triage": "Can it rank and prioritize at all",
        "rollup": "Multi-thread status synthesis",
        "recall": "Do the facts survive summarization",
        "obligations": "What the user owes, and to whom",
        "synthesis": "Inference nobody stated explicitly",
        "scheduling": "Meetings and availability from mail, not calendar",
        "drafting": "Reply generation with the right register",
        "trap": "False premises, spam, and unanswerable questions",
        "consistency": "Same fact, asked twice, same answer",
        "temporal": "Ordering and supersession across the two acts",
    }
    for tier, group in by_tier.items():
        head.append("| {0} | {1} | {2} |".format(tier, len(group), descriptions.get(tier, "")))
    head.append("")

    blocked = [s["id"] for s in specs if s.get("blocked_on_stub")]
    if blocked:
        head.append("⚠️ **{0} evals depend on tools that are still fixture stubs** ".format(len(blocked))
                    + "(`search_context`, `get_thread_brief`, `get_entity_brief`, `list_entities`, "
                      "`find_open_items`). A failure there is a wiring gap, not a model problem: "
                    + ", ".join(blocked))
        head.append("")
    head.append("---")
    head.append("")

    body = [render_eval(spec, (results or {}).get(spec["id"])) for spec in specs]
    return "\n".join(head) + "\n".join(body)


# ------------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default=DEFAULT_API, help="backend base URL")
    parser.add_argument("--tier", help="run only this tier")
    parser.add_argument("--id", dest="eval_id", help="run only this eval id")
    parser.add_argument("--skip-blocked", action="store_true", help="skip evals that depend on stubbed tools")
    parser.add_argument("--render-only", action="store_true", help="emit the blank sheet without calling the backend")
    parser.add_argument("-o", "--out", default="eval-run.md", help="output file (default eval-run.md)")
    args = parser.parse_args()

    data = load_evals()
    specs = data["evals"]

    if args.tier:
        specs = [s for s in specs if s["tier"] == args.tier]
    if args.eval_id:
        specs = [s for s in specs if s["id"] == args.eval_id]
    if args.skip_blocked:
        specs = [s for s in specs if not s.get("blocked_on_stub")]

    if not specs:
        print("No evals matched that filter.", file=sys.stderr)
        return 1

    results: Dict[str, Any] = {}
    if not args.render_only:
        for index, spec in enumerate(specs, 1):
            print("[{0}/{1}] {2}  {3}".format(index, len(specs), spec["id"], spec["prompt"][:64]), file=sys.stderr)
            result = ask(args.api, spec["prompt"])
            results[spec["id"]] = result
            if result["error"]:
                print("        ! {0}".format(result["error"]), file=sys.stderr)

    sheet = render_sheet(data, specs, results, args.api)
    out_path = Path(args.out)
    out_path.write_text(sheet)

    print("\nWrote {0} ({1} evals)".format(out_path, len(specs)), file=sys.stderr)
    if results:
        failed = sum(1 for r in results.values() if r["error"])
        if failed:
            print("{0} of {1} had backend errors — check the sheet.".format(failed, len(results)), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
