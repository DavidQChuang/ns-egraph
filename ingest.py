#!/usr/bin/env python3
"""Convert a markdown run log or handoff into an ns-egraph graph.

Usage: python3 ingest.py SOURCE_MD GRAPH_JSON [--title T] [--origin O] [--max-items 80]

Mapping:
  first "# " heading (or the file name)      -> source node, payload.resource = SOURCE_MD
  "## " section                              -> question "What about <heading>?"
  "- [ ] text" / "- [x] text"                -> work item, open / done
  table data row, "- text" bullet            -> observation that cites the source node
Observations are added while a work item for their section is running. That work item is
set to done once the section ends. Fenced code blocks are skipped entirely.

Each step runs egraph.py as a subprocess: python3 egraph.py GRAPH --actor ingest COMMAND ...
Exit status: 0 when validate reports ok, 1 when it does not, 2 for refused or unreadable input.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
EGRAPH = HERE / "egraph.py"
ACTOR = "ingest"
MAX_QUOTE = 300
MAX_OBSERVATION_TITLE = 120

FENCE_OPEN = re.compile(r"^\s*(`{3,}|~{3,})")
CHECKBOX = re.compile(r"^\s*- \[( |x|X)\](?:\s+(.*))?$")
BULLET = re.compile(r"^\s*- (.*)$")
DEEP_HEADING = re.compile(r"#{3,}\s")
SEPARATOR = re.compile(r"\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?")


class IngestError(Exception):
    pass


def is_separator(stripped: str) -> bool:
    return "|" in stripped and SEPARATOR.fullmatch(stripped) is not None


def parse_markdown(text: str) -> dict:
    """Return the title, section headings, ordered items and skip counts for a markdown file.

    events holds (section index, kind, text) with kind in open, done, observation, in document order.
    """
    title = None
    headings: list[str] = []
    events: list[tuple[int, str, str]] = []
    skipped = Counter()
    current = None
    fence = None
    in_table = False
    header_pending = False

    def add_item(kind: str, body: str) -> None:
        if not body:
            skipped["empty_items"] += 1
        elif current is None:
            skipped["outside_section"] += 1
        else:
            events.append((current, kind, body))

    for line in text.splitlines():
        stripped = line.strip()
        if fence is not None:
            if stripped and stripped.strip(fence[0]) == "" and len(stripped) >= len(fence):
                fence = None
            continue
        opening = FENCE_OPEN.match(line)
        if opening:
            fence = opening.group(1)
            skipped["fenced_code_blocks"] += 1
            in_table = header_pending = False
            continue
        if line.startswith("# "):
            if title is None:
                title = line[2:].strip() or None
            current = None
            in_table = header_pending = False
            continue
        if line.startswith("## "):
            headings.append(line[3:].strip() or "(untitled section)")
            current = len(headings) - 1
            in_table = header_pending = False
            continue
        if DEEP_HEADING.match(line):
            in_table = header_pending = False
            continue
        if stripped.startswith("|"):
            if in_table:
                if re.sub(r"[|\s]", "", stripped):
                    add_item("observation", stripped)
                else:
                    skipped["empty_items"] += 1
            elif header_pending and is_separator(stripped):
                in_table = True
                header_pending = False
            else:
                header_pending = True  # candidate header row; the next line decides
            continue
        in_table = header_pending = False
        if not stripped:
            continue
        box = CHECKBOX.match(line)
        if box:
            add_item("done" if box.group(1).lower() == "x" else "open", (box.group(2) or "").strip())
            continue
        bullet = BULLET.match(line)
        if bullet:
            add_item("observation", bullet.group(1).strip())

    with_events = {index for index, _, _ in events}
    skipped["empty_sections"] = len(headings) - len(with_events)
    return {"title": title, "headings": headings, "events": events, "skipped": skipped}


def run_egraph(graph: Path, args: list[str]) -> dict:
    cmd = [sys.executable, str(EGRAPH), str(graph), "--actor", ACTOR, *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise IngestError(f"egraph {args[0]} printed no JSON: {(proc.stderr or proc.stdout).strip()}")
    if not out.get("ok"):
        message = out.get("error", {}).get("message", out)
        raise IngestError(f"egraph {args[0]} failed: {message}")
    return out


def validate(graph: Path) -> dict:
    cmd = [sys.executable, str(EGRAPH), str(graph), "--actor", ACTOR, "validate"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise IngestError(f"egraph validate printed no JSON: {(proc.stderr or proc.stdout).strip()}")


def convert(source_arg: str, graph: Path, title: str | None, origin: str | None, max_items: int) -> dict:
    parsed = parse_markdown(Path(source_arg).read_text(encoding="utf-8-sig"))
    headings, events, skipped = parsed["headings"], parsed["events"], parsed["skipped"]
    source_title = parsed["title"] or Path(source_arg).name
    graph_title = title or source_title
    run_egraph(graph, ["init", f"--title={graph_title}", f"--origin={origin or source_title}"])
    payload = json.dumps({"resource": source_arg})
    src = run_egraph(graph, ["add", "source", f"--title={source_title}", f"--payload={payload}"])["data"]["id"]

    counts = Counter()
    questions: dict[int, str] = {}
    current_section = None
    extract = None  # id of the running work item that yields observations for the current section
    stopped_by_cap = False

    def close_extract() -> None:
        nonlocal extract
        if extract is not None:
            run_egraph(graph, ["state", extract, "done"])
            extract = None

    for index, kind, body in events:
        if counts["observations"] >= max_items:
            skipped["over_max_items"] += 1
            stopped_by_cap = True
            continue
        if index != current_section:
            close_extract()
            current_section = index
        if index not in questions:
            heading = headings[index]
            questions[index] = run_egraph(graph, ["add", "question", f"--title=What about {heading}?"])["data"]["id"]
            counts["questions"] += 1
        question = questions[index]
        if kind in ("open", "done"):
            work = run_egraph(graph, ["add", "work", f"--title={body}", f"--from={question}"])["data"]["id"]
            if kind == "done":
                run_egraph(graph, ["state", work, "done"])
                counts["work_done"] += 1
            else:
                counts["work_open"] += 1
            continue
        if extract is None:
            work = run_egraph(graph, ["add", "work", f"--title=Record observations from {headings[index]}",
                                      f"--from={question}"])["data"]["id"]
            run_egraph(graph, ["state", work, "running"])
            extract = work
            counts["observation_work"] += 1
        run_egraph(graph, ["add", "observation", f"--title={body[:MAX_OBSERVATION_TITLE]}",
                           f"--quote={body[:MAX_QUOTE]}", f"--by={extract}", f"--cites={src}"])
        counts["observations"] += 1
    close_extract()

    result = validate(graph)
    return {
        "source": source_arg,
        "graph": str(graph),
        "title": graph_title,
        "counts": {"sources": 1, "questions": counts["questions"], "work_open": counts["work_open"],
                   "work_done": counts["work_done"], "observation_work": counts["observation_work"],
                   "observations": counts["observations"]},
        "skipped": {"empty_sections": skipped["empty_sections"], "empty_items": skipped["empty_items"],
                    "outside_section": skipped["outside_section"], "over_max_items": skipped["over_max_items"],
                    "fenced_code_blocks": skipped["fenced_code_blocks"]},
        "max_items": max_items,
        "stopped_at_max_items": stopped_by_cap,
        "validate": {"ok": result["ok"], "errors": result["errors"], "warnings": result["warnings"]},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source_md", help="markdown run log or handoff to read")
    ap.add_argument("graph_json", type=Path, help="new graph file to create; must not exist")
    ap.add_argument("--title", help="graph title (default: the source title)")
    ap.add_argument("--origin", help="origin statement (default: the document title)")
    ap.add_argument("--max-items", type=int, default=80, help="stop after this many observations (default 80)")
    a = ap.parse_args(argv)
    if a.max_items < 1:
        ap.error("--max-items must be at least 1")
    if not Path(a.source_md).is_file():
        print(f"error: source file not found: {a.source_md}", file=sys.stderr)
        return 2
    if a.graph_json.exists():
        print(f"error: {a.graph_json} already exists; give a new GRAPH_JSON path", file=sys.stderr)
        return 2
    try:
        summary = convert(a.source_md, a.graph_json, a.title, a.origin, a.max_items)
    except (IngestError, OSError, UnicodeDecodeError) as e:
        a.graph_json.unlink(missing_ok=True)  # this run created the file, so remove the partial graph
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    return 0 if summary["validate"]["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
