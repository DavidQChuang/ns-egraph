"""Compare recall and checkpoint behavior on a small synthetic development set."""

import argparse
import json
import sys
from pathlib import Path
from shutil import copytree
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter

from egraph import GraphError
from evals.fixtures import prepare
from integrations.claude.hook import context as claude_context
from integrations.kiro.hook import context as kiro_context
from recall import build_index, retrieve

HOSTS = {"claude": claude_context, "kiro": kiro_context}
CASES = Path(__file__).with_name("cases.json")


def grade(case: dict, output: str) -> list[str]:
    if case.get("silent"):
        return ["unexpected context"] if output else []
    if not output:
        return ["missing context"]

    problems = [f"missing {text!r}" for text in case["required"] if text not in output]
    problems.extend(f"unexpected {text!r}" for text in case.get("forbidden", []) if text in output)
    if len(output) > 1500:
        problems.append("context exceeds 1,500 characters")
    if case.get("host") and not (
        output.startswith("<ns_egraph_recall>\n") and output.endswith("</ns_egraph_recall>")
    ):
        problems.append("missing hook wrapper")
    return problems


def rollout(root: Path, case: dict, mode: str) -> dict:
    start = perf_counter()
    if "host" in case:
        host = case["host"]
        output = HOSTS[host](root, case["event"])
        if host == "claude" and output:
            output = json.loads(output)["hookSpecificOutput"]["additionalContext"]
        method = "checkpoint" if output else ""
    else:
        method, output = retrieve(root, case["prompt"], semantic=mode == "lexical+semantic")
    return {
        "case": case,
        "mode": mode,
        "method": method,
        "elapsed_ms": round((perf_counter() - start) * 1000, 2),
        "output": output,
        "problems": grade(case, output),
    }


def summarize(traces: list[dict]) -> None:
    for mode in dict.fromkeys(trace["mode"] for trace in traces):
        selected = [trace for trace in traces if trace["mode"] == mode]
        passed = sum(not trace["problems"] for trace in selected)
        latency = median(trace["elapsed_ms"] for trace in selected)
        print(f"{mode}: {passed}/{len(selected)} passed, median {latency:.1f} ms")
        for category in dict.fromkeys(trace["case"]["category"] for trace in selected):
            group = [trace for trace in selected if trace["case"]["category"] == category]
            print(f"  {category}: {sum(not trace['problems'] for trace in group)}/{len(group)}")
            for trace in group:
                if trace["problems"]:
                    print(f"    {trace['case']['id']}: {', '.join(trace['problems'])}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-cache", type=Path,
                        help="also evaluate semantic fallback using an existing .semantic/models directory")
    parser.add_argument("--output", type=Path, help="save case expectations, output, and timing as JSON")
    args = parser.parse_args()

    cases = json.loads(CASES.read_text(encoding="utf-8"))
    retrieval = [case for case in cases if "host" not in case]
    checkpoints = [case for case in cases if "host" in case]
    backend = None
    try:
        with TemporaryDirectory(prefix="ns-egraph-evals-") as directory:
            root = Path(directory)
            prepare(root)
            traces = [rollout(root, case, "lexical") for case in retrieval]
            if args.model_cache:
                from semantic_recall import backend_id

                copytree(args.model_cache, root / ".semantic" / "models", symlinks=True)
                build_index(root)
                backend = backend_id()
                traces.extend(rollout(root, case, "lexical+semantic") for case in retrieval)
            traces.extend(rollout(root, case, "hooks") for case in checkpoints)
        summarize(traces)
        if args.output:
            report = {"suite": "synthetic-recall/1", "python": sys.version.split()[0],
                      "embedding_backend": backend, "traces": traces}
            args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except (GraphError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
