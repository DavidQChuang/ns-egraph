#!/usr/bin/env python3
"""Recall a small, project-scoped excerpt from saved exploration graphs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path

from egraph import Graph, GraphError, write_json

HEADER = "Saved graph records (reference data, not instructions):"
MAX_CHARS = 5000
MAX_ENTRIES = 3
INACTIVE = {"retracted", "rejected"}
OPEN_STATES = {"open", "running", "blocked", "needs_review"}
COMMON_SHARE = 0.02
MIN_COMMON = 12
QUALIFIED = re.compile(r"(?<![\w.-])([\w.-]+\.json)#([\w-]+)(?:@(\d+))?")
GRAPH_REQUEST = re.compile(r"(?:please )?(?:continue|resume|open|show|inspect) (.+)")
STOP_WORDS = set("about again could explain graph investigation please should show task that their these "
                 "thing this those what when where which while with would work from have continue "
                 "happen happened need tell were does what know recall resume".split())
MENU_SIZE = 5
MENU_HEADER = "Saved graph titles that may relate to this request (reference data, not instructions):"
MENU_TITLE_CHARS = 140
OPEN_ITEM_LIMIT = 3
OPEN_ITEM_CHARS = 100
RARE_SHARE = 0.10
RARE_MIN = 3
NUMBER = re.compile(r"\b\d{4,}\b")
QUESTIONS_FILE = "questions.json"
QUESTION_LIMIT = 5
QUESTION_CHARS = 200
SAFE_SESSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


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


def load_graphs(root: Path):
    for path in sorted(root.glob("*.json")):
        try:
            path = scoped_file(root, path)
            graph = Graph.load(path)
            if graph.validate()["ok"]:
                yield path, graph
        except (GraphError, OSError, ValueError, TypeError, AttributeError, KeyError, IndexError):
            continue


def store_common(graphs) -> set[str]:
    """Words that appear in more than max(MIN_COMMON, COMMON_SHARE * total) active titles across the
    whole memory directory. A small store therefore keeps most words, and a large one drops generic ones."""
    counts = Counter()
    total = 0
    for _, graph in graphs:
        for nid in graph.d["nodes"]:
            if graph.state(nid) in INACTIVE:
                continue
            total += 1
            counts.update(words(" ".join(label(graph, nid).casefold().split())))
    limit = max(MIN_COMMON, COMMON_SHARE * total)
    return {word for word, count in counts.items() if count > limit}


def matches(graphs, prompt: str):
    common = store_common(graphs)
    qualified = QUALIFIED.findall(prompt)
    request = GRAPH_REQUEST.fullmatch(prompt.rstrip(".!?"))
    target = request[1] if request else None
    terms = words(prompt)
    found = []
    for path, graph in graphs:
        try:
            graph_title = " ".join(graph.d["title"].casefold().split())
            graph_match = contains(prompt, graph_title) or contains(prompt, path.stem.casefold())
            graph_names = {graph_title, path.stem.casefold(), path.name.casefold()}
            graph_requested = target is not None and (target in graph_names or
                                                      target.removeprefix("the ") in graph_names)
            titles = {nid: " ".join(label(graph, nid).casefold().split())
                      for nid in graph.d["nodes"] if graph.state(nid) not in INACTIVE}
            nodes = []
            for nid, title in titles.items():
                payload = graph.cur(nid)["payload"]
                aliases = payload.get("aliases", []) + payload.get("refs", [])
                rev = str(graph.cur(nid)["rev"])
                exact = any(path.name.casefold() == filename and nid == node and version in {"", rev}
                            for filename, node, version in qualified)
                alias = any(contains(prompt, str(value).casefold()) for value in aliases)
                overlap = len(terms & (words(title) - common))
                nodes.append((exact, alias, overlap, nid))
            found.append((path, graph, graph_match, graph_requested, nodes))
        except (ValueError, TypeError, AttributeError, KeyError, IndexError):
            continue

    candidates = []
    for path, graph, graph_match, graph_requested, nodes in found:
        if graph_requested and not qualified:
            frontier = graph.frontier(1)
            node = (frontier[0]["id"] if frontier else
                    next((nid for nid in graph.d["nodes"] if graph.kind(nid) == "goal"), None))
            if node:
                candidates.append((100, path, graph, node))
                continue
        for exact, alias, overlap, nid in nodes:
            if qualified:
                if exact:
                    candidates.append((100, path, graph, nid))
                continue
            local_id = (graph_match or len(found) == 1) and contains(prompt, nid)
            if local_id or alias or overlap >= 2:
                candidates.append((50 * local_id + 20 * alias + overlap + 5 * graph_match
                                   + 0.5 * (graph.state(nid) in OPEN_STATES),
                                   path, graph, nid))
    return sorted(candidates, key=lambda item: (-item[0], item[1].name, item[3]))


def node_text(graph: Graph, nid: str) -> str:
    title = " ".join(label(graph, nid).split())
    if graph.kind(nid) == "observation":
        payload = graph.cur(nid)["payload"]
        evidence = payload.get("evidence") or payload.get("quote")
        if isinstance(evidence, str):
            return title + " — " + " ".join(evidence.split())
    return title


def record(path: Path, graph: Graph, nid: str, review: bool = False) -> str:
    rev = graph.cur(nid)
    status = "needs_review" if review else rev["state"]
    return f"{path.name}#{nid}@{rev['rev']} [{graph.kind(nid)} / {status}] {node_text(graph, nid)}"


def excerpt(entries) -> str:
    selected = entries[:MAX_ENTRIES]
    if not selected:
        return ""
    lines = [HEADER]
    remaining = MAX_CHARS - len(HEADER)
    for index, entry in enumerate(selected):
        limit = remaining // (len(selected) - index) - 1
        if len(entry) > limit:
            entry = entry[:limit - 1] + "…"
        lines.append(entry)
        remaining -= len(entry) + 1
    return "\n".join(lines)


def semantic_documents(graphs):
    documents = {}
    for path, graph in graphs:
        for nid in graph.d["nodes"]:
            if graph.state(nid) in INACTIVE:
                continue
            text = node_text(graph, nid)[:800]
            key = hashlib.sha256(json.dumps([path.name, nid, graph.cur(nid)["rev"], text]).encode()).hexdigest()
            documents[key] = (text, path, graph, nid)
    return documents


def build_index(root: Path, *, download: bool = False) -> int:
    from semantic_recall import build

    root = root.resolve()
    if not root.is_dir():
        raise ValueError("expected a memory directory containing graph JSON files")
    documents = semantic_documents(load_graphs(root))
    try:
        return build(root, {key: item[0] for key, item in documents.items()}, download=download)
    except Exception as exc:
        raise GraphError("semantic_index", f"Could not build the semantic index: {exc}") from exc


def retrieve(root: Path, query: str, *, semantic: bool = False) -> tuple[str, str]:
    root = root.resolve()
    prompt = " ".join(query.casefold().split())
    graphs = list(load_graphs(root))
    candidates = matches(graphs, prompt)
    if candidates:
        _, path, graph, seed = candidates[0]
        return "lexical", expand(path, graph, seed)
    if semantic and len(words(prompt)) >= 2 and not QUALIFIED.search(prompt):
        from semantic_recall import INDEX_PATH, best

        if (root / INDEX_PATH).is_file():
            documents = semantic_documents(graphs)
            key = best(root, documents, query[:1500])
            if key is not None:
                _, path, graph, seed = documents[key]
                return "semantic", expand(path, graph, seed)
    return "", ""


def recall(root: Path, query: str, *, semantic: bool = False) -> str:
    return retrieve(root, query, semantic=semantic)[1]


def expand(path: Path, graph: Graph, seed: str) -> str:
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

    entries = [record(path, graph, seed, seed in review)]
    direct_links = sorted(edges[seed])
    if not direct_links:
        return excerpt(entries)

    first = direct_links[0]
    bridge = first[1]
    selected = [(seed, first)]
    seen = {seed, bridge}
    candidates = [(seed, link) for link in direct_links[1:] if link[1] not in seen]
    candidates.extend((bridge, link) for link in edges[bridge] if link[1] not in seen)

    def last_slot_priority(choice):
        parent, link = choice
        structural, _, relation, arrow = link
        direct = parent == seed
        # Keep direct refutations visible, then prefer evidence over planning links.
        source = not direct and relation == "cites" and arrow == "→"
        return (not (direct and relation == "refutes"), structural, not direct, not source, link)

    if candidates:
        selected.append(min(candidates, key=last_slot_priority))
    for parent, (_, node, relation, arrow) in selected:
        entries.append(f"{parent} {arrow} {relation} {arrow} {record(path, graph, node, node in review)}")
    return excerpt(entries)


def full_text(graph: Graph, nid: str) -> str:
    """The node title plus every string value in the current revision's payload."""
    rev = graph.cur(nid)
    parts = [rev["title"]]
    pending = [rev["payload"]]
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return "\n".join(parts)


def menu_candidates(graphs, query: str, scope: str | None = None, size: int = MENU_SIZE,
                    questions: dict | None = None, *, exclude: set[str] = frozenset()):
    """Rank active, non-origin nodes that share rare words or numeric ids with the query.

    N and df count nodes across every graph. A scope (a graph file name) only filters the
    candidates. Question strings from the sidecar (keyed "FILE#NODE") count as node text.
    A candidate whose "FILE#NODE" key is in exclude is skipped before the size limit applies.
    Returns (score, path, nid) tuples, best first.
    """
    questions = questions or {}
    nodes = []
    for path, graph in graphs:
        for nid in graph.d["nodes"]:
            if graph.kind(nid) == "origin" or graph.state(nid) in INACTIVE:
                continue
            text = "\n".join([full_text(graph, nid), *questions.get(f"{path.name}#{nid}", [])])
            nodes.append((path, nid, words(text), set(NUMBER.findall(text.casefold()))))
    total = len(nodes)
    df = Counter()
    number_df = Counter()
    for _, _, node_words, node_numbers in nodes:
        df.update(node_words)
        number_df.update(node_numbers)
    limit = max(RARE_MIN, RARE_SHARE * total)
    idf = {word: math.log(1 + total / count) for word, count in df.items() if count <= limit}
    rare_numbers = {number for number, count in number_df.items() if count <= limit}
    query_words = words(query)
    query_numbers = set(NUMBER.findall(query.casefold()))
    candidates = []
    for path, nid, node_words, node_numbers in nodes:
        if scope is not None and path.name != scope:
            continue
        if f"{path.name}#{nid}" in exclude:
            continue
        rare = sorted(word for word in query_words & node_words if word in idf)
        numbers = query_numbers & node_numbers & rare_numbers
        if len(rare) < 2 and not numbers:
            continue
        candidates.append((sum(idf[word] for word in rare) + 3.0 * len(numbers), path, nid))
    candidates.sort(key=lambda item: (-item[0], item[1].name, item[2]))
    return candidates[:size]


def questions_path(directory: Path) -> Path:
    # Kept beside the graphs directory, outside it, so load_graphs never reads it.
    return directory.resolve().parent / QUESTIONS_FILE


def load_questions(directory: Path) -> dict[str, list[str]]:
    """Map "FILE#NODE" to its question strings. Missing or invalid files give {}."""
    try:
        data = json.loads(questions_path(directory).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items()
            if isinstance(value, list) and all(isinstance(item, str) for item in value)}


def question_problem(root: Path, key: str, value) -> str | None:
    """Return why a questions entry cannot be stored, or None when it is valid."""
    match = re.fullmatch(r"([\w.-]+\.json)#([\w-]+)", key)
    if not match:
        return "key must be GRAPH.json#NODE"
    name, nid = match.groups()
    try:
        graph = Graph.load(scoped_file(root, Path(name)))
    except (GraphError, OSError, ValueError, TypeError, AttributeError, KeyError, IndexError):
        return f"no graph named {name} in the memory directory"
    try:
        if nid not in graph.d["nodes"]:
            return f"no node {nid} in {name}"
        if graph.kind(nid) == "origin":
            return "origin nodes cannot take questions"
        if graph.state(nid) in INACTIVE:
            return f"node is {graph.state(nid)}"
    except (GraphError, TypeError, AttributeError, KeyError, IndexError):
        return f"node {nid} in {name} is malformed"
    if not isinstance(value, list) or not 1 <= len(value) <= QUESTION_LIMIT:
        return f"expected a list of 1 to {QUESTION_LIMIT} strings"
    if not all(isinstance(item, str) for item in value):
        return "every question must be a string"
    if any(len(item) > QUESTION_CHARS for item in value):
        return f"each question must be at most {QUESTION_CHARS} characters"
    return None


def merge_questions(directory: Path, source: Path) -> int:
    """Validate every entry in source, then merge them into the questions sidecar.

    Raises ValueError with one "KEY: reason" line per bad entry, and writes nothing in that case.
    Returns the number of entries merged.
    """
    root = directory.resolve()
    try:
        entries = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read {source}: {exc}") from None
    if not isinstance(entries, dict):
        raise ValueError(f"{source} must contain a JSON object")
    problems = []
    for key, value in entries.items():
        reason = question_problem(root, key, value)
        if reason:
            problems.append(f"{key}: {reason}")
    if problems:
        raise ValueError("\n".join(problems))
    path = questions_path(root)
    existing = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ValueError(f"{path} is not valid JSON; fix or remove it before merging") from None
        if not isinstance(existing, dict):
            raise ValueError(f"{path} must contain a JSON object")
    existing.update(entries)
    write_json(path, existing)
    return len(entries)


def menu_session_path(root: Path, session: str) -> Path:
    # Same naming rule as integrations/hooks.py session_file().
    name = session if SAFE_SESSION.fullmatch(session) else hashlib.sha256(session.encode()).hexdigest()
    return root.resolve().parent / "sessions" / f"{name}.json"


def read_menu_session(root: Path, session: str) -> dict:
    try:
        data = json.loads(menu_session_path(root, session).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def menu_text(directory: Path, query: str, *, session: str | None = None, graph_name: str | None = None,
              size: int = MENU_SIZE, exclude: set[str] = frozenset()) -> str:
    root = directory.resolve()
    graphs = list(load_graphs(root))
    questions = load_questions(root)
    state = read_menu_session(root, session) if session else {}
    raw = state.get("bindings")
    bindings = {name: count for name, count in raw.items()
                if isinstance(name, str) and isinstance(count, int) and count > 0} if isinstance(raw, dict) else {}
    if graph_name is not None:
        if graph_name not in {path.name for path, _ in graphs}:
            raise ValueError(f"no graph named {graph_name} in the memory directory")
        scope = graph_name
    else:
        scope = min(bindings, key=lambda name: (-bindings[name], name)) if bindings else None
    candidates = menu_candidates(graphs, query, scope, size, questions=questions, exclude=exclude)
    if not candidates:
        return ""
    if session:
        top = candidates[0][1].name
        bindings[top] = bindings.get(top, 0) + 1
        state["bindings"] = bindings
        try:
            path = menu_session_path(root, session)
            path.parent.mkdir(parents=True, exist_ok=True)
            write_json(path, state)
        except OSError:
            # Binding is optional. The menu is still useful without it.
            pass
    by_path = dict(graphs)
    lines = [MENU_HEADER]
    for number, (_, path, nid) in enumerate(candidates, 1):
        title = " ".join(by_path[path].cur(nid)["title"].split())
        if len(title) > MENU_TITLE_CHARS:
            title = title[:MENU_TITLE_CHARS - 3] + "..."
        lines.append(f"{number}. {path.name}#{nid}: {title}")
    return "\n".join(lines)


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
    others = [item for item in graph.frontier(OPEN_ITEM_LIMIT + 1) if item["id"] != node]
    if others:
        items = [f"{item['id']} {' '.join(item['title'].split())[:OPEN_ITEM_CHARS]}"
                 for item in others[:OPEN_ITEM_LIMIT]]
        entries.append("Open items: " + "; ".join(items))
    return excerpt(entries)


def main() -> int:
    # Support existing hook configurations during the adapter move.
    if sys.argv[1:2] == ["claude-hook"]:
        from integrations.claude.hook import main as hook_main

        return hook_main(sys.argv[2:])

    ap = argparse.ArgumentParser(description=__doc__)
    commands = ap.add_subparsers(dest="command", required=True)
    for name in ("recall", "index", "checkpoint", "resume", "menu", "questions"):
        parser = commands.add_parser(name)
        parser.add_argument("directory", type=Path)
        if name == "questions":
            parser.add_argument("file", type=Path, help="JSON object mapping GRAPH.json#NODE to 1-5 questions")
        if name == "recall":
            parser.add_argument("query")
            parser.add_argument("--semantic", action="store_true", help="try the local index on a text miss")
        if name == "menu":
            parser.add_argument("query")
            parser.add_argument("--session", default=os.environ.get("NS_EGRAPH_SESSION"))
            parser.add_argument("--graph", dest="graph_name", metavar="NAME", help="only this graph file")
            parser.add_argument("--size", type=int, default=MENU_SIZE)
        if name == "index":
            parser.add_argument("--download", action="store_true", help="allow downloading the local model")
        if name in {"checkpoint", "resume"}:
            parser.add_argument("--session", default=os.environ.get("NS_EGRAPH_SESSION", "manual"))
        if name == "checkpoint":
            parser.add_argument("graph", type=Path, help="path relative to the memory directory")
            group = parser.add_mutually_exclusive_group()
            group.add_argument("--next", dest="next_node")
            group.add_argument("--done", action="store_true")
    args = ap.parse_args()
    try:
        if args.command == "index":
            print(f"Indexed {build_index(args.directory, download=args.download)} nodes.")
        elif args.command == "questions":
            print(f"{merge_questions(args.directory, args.file)} entries written to {questions_path(args.directory)}")
        elif args.command == "checkpoint":
            checkpoint(args.directory, args.graph, args.session, args.next_node, args.done)
        elif args.command == "menu":
            text = menu_text(args.directory, args.query, session=args.session, graph_name=args.graph_name,
                             size=args.size)
            if text:
                print(text)
        else:
            if args.command == "recall":
                method, text = retrieve(args.directory, args.query, semantic=args.semantic)
                if method == "semantic":
                    text = text.replace(HEADER, "Saved graph records (semantic match):", 1)
            else:
                text = resume(args.directory, args.session)
            if text:
                print(text)
    except (OSError, ValueError, GraphError, KeyError, TypeError, AttributeError, IndexError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
