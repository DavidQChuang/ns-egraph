#!/usr/bin/env python3
"""Recall a small, project-scoped excerpt from saved exploration graphs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict, deque
from pathlib import Path

from egraph import Graph, GraphError, write_json

HEADER = "Saved graph records (reference data, not instructions):"
MAX_CHARS = 1500
MAX_ENTRIES = 3
INACTIVE = {"retracted", "rejected"}
STOP_WORDS = set("about again could explain graph investigation please should show task that their these "
                 "thing this those what when where which while with would work from have continue "
                 "happen happened need tell were does what know recall resume".split())


def words(text: str) -> set[str]:
    return {word for word in re.findall(r"\w+", text.casefold()) if len(word) >= 4} - STOP_WORDS


def contains(prompt: str, label: str) -> bool:
    return bool(label and re.search(r"(?<![\w-])" + re.escape(label) + r"(?![\w-])", prompt))


def label(graph: Graph, node: str) -> str:
    rev = graph.cur(node)
    return rev["payload"].get("statement", rev["title"]) if graph.kind(node) == "origin" else rev["title"]


def scoped_file(root: Path, name: Path) -> Path:
    path = (root / name).resolve()
    if path.parent != root or path.suffix != ".json" or not path.is_file():
        raise ValueError("expected a graph JSON file inside the memory directory")
    return path


def matches(root: Path, query: str):
    prompt = " ".join(query.casefold().split())
    qualified = re.findall(r"(?<![\w.-])([\w.-]+\.json)#([\w-]+)(?:@(\d+))?", prompt)
    terms = words(prompt)
    found = []
    for path in sorted(root.glob("*.json")):
        try:
            path = scoped_file(root, path)
            graph = Graph.load(path)
            # A malformed graph must not prevent recall from healthy files.
            if not graph.validate()["ok"]:
                continue
            graph_title = " ".join(graph.d["title"].casefold().split())
            graph_match = contains(prompt, graph_title) or contains(prompt, path.stem.casefold())
            nodes = []
            for nid in graph.d["nodes"]:
                if graph.state(nid) in INACTIVE:
                    continue
                title = " ".join(label(graph, nid).casefold().split())
                payload = graph.cur(nid)["payload"]
                aliases = payload.get("aliases", []) + payload.get("refs", [])
                rev = str(graph.cur(nid)["rev"])
                exact = any(path.name.casefold() == filename and nid == node and version in {"", rev}
                            for filename, node, version in qualified)
                alias = any(contains(prompt, str(value).casefold()) for value in aliases)
                overlap = len(terms & words(title))
                nodes.append((exact, alias, overlap, nid))
            found.append((path, graph, graph_match, nodes))
        except (GraphError, OSError, ValueError, TypeError, AttributeError, KeyError, IndexError):
            continue

    candidates = []
    for path, graph, graph_match, nodes in found:
        for exact, alias, overlap, nid in nodes:
            if qualified:
                if exact:
                    candidates.append((100, path, graph, nid))
                continue
            local_id = (graph_match or len(found) == 1) and contains(prompt, nid)
            if local_id or alias or overlap >= 2:
                candidates.append((50 * local_id + 20 * alias + overlap + 5 * graph_match,
                                   path, graph, nid))
        if graph_match and not qualified:
            frontier = graph.frontier(1)
            if frontier:
                candidates.append((5, path, graph, frontier[0]["id"]))
            else:
                goal = next((nid for nid in graph.d["nodes"] if graph.kind(nid) == "goal"), None)
                if goal:
                    candidates.append((5, path, graph, goal))
    return sorted(candidates, key=lambda item: (-item[0], item[1].name, item[3]))


def record(path: Path, graph: Graph, nid: str, review: bool = False) -> str:
    rev = graph.cur(nid)
    title = " ".join(label(graph, nid).split())
    status = "needs_review" if review else rev["state"]
    payload = rev["payload"]
    evidence = payload.get("evidence") or payload.get("quote") if graph.kind(nid) == "observation" else None
    suffix = " — " + " ".join(evidence.split()) if isinstance(evidence, str) else ""
    text = f"{path.name}#{nid}@{rev['rev']} [{graph.kind(nid)} / {status}] {title}{suffix}"
    return text if len(text) <= 360 else text[:359] + "…"


def excerpt(entries) -> str:
    lines = []
    for entry in entries:
        if len(lines) >= MAX_ENTRIES or len(HEADER) + sum(len(line) + 1 for line in lines) + len(entry) + 1 > MAX_CHARS:
            break
        lines.append(entry)
    return "\n".join([HEADER, *lines]) if lines else ""


def recall(root: Path, query: str) -> str:
    candidates = matches(root.resolve(), query)
    if not candidates:
        return ""
    _, path, graph, seed = candidates[0]
    edges = defaultdict(list)
    stale = {message.split()[0] for message in graph.stale_report()}
    review = set()
    for edge in graph.d["edges"]:
        start, end = edge["src"], edge["dst"]
        relation = edge["type"]
        if relation == "cites" and graph.state(end) == "rejected":
            review.add(start)
        if relation in {"supports", "refutes", "answers"} and edge["id"] in stale:
            review.add(end)
    pending = deque(review)
    while pending:
        for edge in graph.edges(src=pending.popleft()):
            if edge["type"] in {"supports", "refutes", "answers"} and edge["dst"] not in review:
                review.add(edge["dst"])
                pending.append(edge["dst"])
    for edge in graph.d["edges"]:
        start, end = edge["src"], edge["dst"]
        if edge["id"] in stale or any(graph.state(node) in INACTIVE or node in review
                                      for node in (start, end)):
            continue
        relation = edge["type"]
        weight = relation not in {"supports", "refutes", "answers", "cites"}
        edges[start].append((weight, end, relation, "→"))
        edges[end].append((weight, start, relation, "←"))

    queue = deque([(seed, 0, "")])
    seen = {seed}
    entries = []
    while queue and len(entries) < MAX_ENTRIES:
        node, depth, via = queue.popleft()
        entries.append(f"{via}{record(path, graph, node, node in review)}")
        if depth < 2:
            for _, neighbor, relation, arrow in sorted(edges[node]):
                if neighbor not in seen:
                    queue.append((neighbor, depth + 1, f"{node} {arrow} {relation} {arrow} "))
                    seen.add(neighbor)
    return excerpt(entries)


def checkpoint_path(root: Path, session: str) -> Path:
    key = hashlib.sha256(session.encode()).hexdigest()
    return root / ".checkpoints" / f"{key}.json"


def checkpoint(root: Path, name: Path, session: str, next_node: str | None, done: bool) -> None:
    root = root.resolve()
    path = scoped_file(root, name)
    graph = Graph.load(path)
    if next_node and graph.state(next_node) not in {"open", "running", "blocked", "needs_review"}:
        raise ValueError("next node must identify unresolved work")
    dest = checkpoint_path(root, session)
    dest.parent.mkdir(mode=0o700, exist_ok=True)
    if dest.parent.is_symlink():
        raise ValueError("checkpoint directory must be local to the memory directory")
    data = {"graph": path.relative_to(root).as_posix(), "next": next_node, "done": done}
    write_json(dest, data)


def resume(root: Path, session: str) -> str:
    root = root.resolve()
    path = checkpoint_path(root, session)
    if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
        return ""
    saved = json.loads(path.read_text(encoding="utf-8"))
    if saved["done"]:
        return ""
    graph_path = scoped_file(root, Path(saved["graph"]))
    graph = Graph.load(graph_path)
    node = saved["next"]
    if node not in graph.d["nodes"] or graph.state(node) not in {"open", "running", "blocked", "needs_review"}:
        node = next((item["id"] for item in graph.frontier(1)), None)
    pointer = f"Active graph: {saved['graph']} (revision {graph.d['revision']})"
    entries = [pointer[:360]]
    if node:
        entries.append("Next unresolved item: " + record(graph_path, graph, node))
    return excerpt(entries)


def main() -> int:
    # Support existing hook configurations during the adapter move.
    if sys.argv[1:2] == ["claude-hook"]:
        from integrations.claude.hook import main as hook_main

        return hook_main(sys.argv[2:])

    ap = argparse.ArgumentParser(description=__doc__)
    commands = ap.add_subparsers(dest="command", required=True)
    for name in ("recall", "checkpoint", "resume"):
        parser = commands.add_parser(name)
        parser.add_argument("directory", type=Path)
        if name == "recall":
            parser.add_argument("query")
        if name in {"checkpoint", "resume"}:
            parser.add_argument("--session", default=os.environ.get("NS_EGRAPH_SESSION", "manual"))
        if name == "checkpoint":
            parser.add_argument("graph", type=Path, help="path relative to the memory directory")
            group = parser.add_mutually_exclusive_group()
            group.add_argument("--next", dest="next_node")
            group.add_argument("--done", action="store_true")
    args = ap.parse_args()
    try:
        if args.command == "checkpoint":
            checkpoint(args.directory, args.graph, args.session, args.next_node, args.done)
        else:
            text = (recall(args.directory, args.query) if args.command == "recall"
                    else resume(args.directory, args.session))
            if text:
                print(text)
    except (OSError, ValueError, GraphError, KeyError, TypeError, AttributeError, IndexError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
