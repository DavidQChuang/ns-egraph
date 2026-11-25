#!/usr/bin/env python3
"""Track an exploration in a single JSON file using revisioned nodes and typed edges.

Use questions and hypotheses to plan work. Record sources and observations,
build claims from evidence, and explicitly accept a goal against that evidence.
Run "egraph.py GRAPH COMMAND --help" for individual commands.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import tempfile
import textwrap
from pathlib import Path

SCHEMA = "ns-egraph/1"
LEGACY_SCHEMA = "ai-researcher-egraph/1"
DEFAULT_ACTOR = "user"

KINDS = {
    "origin": {"recorded"},
    "goal": {"open", "met", "abandoned", "needs_review"},
    "question": {"open", "answered", "parked"},
    "hypothesis": {"open", "supported", "refuted", "inconclusive"},
    "work": {"open", "running", "done", "blocked", "stopped"},
    "source": {"unverified", "verified", "rejected"},
    "observation": {"active", "retracted"},
    "claim": {"active", "retracted"},
    "hint": {"active", "consumed"},
}
INITIAL = {"origin": "recorded", "goal": "open", "question": "open", "hypothesis": "open", "work": "open",
           "source": "unverified", "observation": "active", "claim": "active", "hint": "active"}
EVIDENCE = {"observation", "claim"}
FRONTIER_KINDS = {"question", "hypothesis", "work"}
EPISTEMIC = {"observed", "inferred", "reported"}

# edge type -> (allowed source kinds, allowed destination kinds, revision-pinned?)
EDGES = {
    "spawns": ({"origin"}, {"goal"}, True),
    "derives": ({"origin", "goal", "question", "hypothesis", "observation", "claim", "hint"},
                {"question", "hypothesis", "work"}, True),
    "yields": ({"work"}, {"observation", "claim", "source"}, True),
    "cites": ({"observation", "claim"}, {"source"}, True),
    "supports": (EVIDENCE, {"claim", "hypothesis", "goal"}, True),
    "refutes": (EVIDENCE, {"claim", "hypothesis"}, True),
    "answers": (EVIDENCE, {"question"}, True),
    "depends_on": ({"work"}, {"work"}, False),
}
WORK_TRANSITIONS = {"open": {"running", "stopped", "done"}, "running": {"done", "blocked", "stopped", "open"},
                    "blocked": {"open", "stopped"}, "stopped": {"open"}, "done": set()}
PREFIX = {"origin": "origin", "goal": "goal", "question": "q", "hypothesis": "h", "work": "w", "source": "src",
          "observation": "obs", "claim": "c", "hint": "hint"}


class GraphError(Exception):
    def __init__(self, code: str, msg: str):
        super().__init__(f"{code}: {msg}")
        self.code = code


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: Path, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=1, ensure_ascii=False)
            stream.write("\n")
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


# ------------------------------------------------------------------ store
class Graph:
    def __init__(self, data: dict):
        self.d = data

    # -- io
    @classmethod
    def load(cls, path: Path) -> "Graph":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema") not in (SCHEMA, LEGACY_SCHEMA):
            raise GraphError("unsupported_schema", f"expected {SCHEMA} or a supported legacy schema")
        return cls(data)

    def save(self, path: Path) -> None:
        write_json(path, self.d)

    @classmethod
    def new(cls, title: str, origin: str, actor: str = DEFAULT_ACTOR) -> "Graph":
        g = cls({"schema": SCHEMA, "title": title, "created": _now(), "revision": 0, "counter": 0,
                 "nodes": {}, "edges": [], "acceptances": [], "events": []})
        g._add_node("origin", "Origin", {"statement": origin, "note": "request/background; not verified evidence"},
                    actor, "CreateGraph")
        return g

    # -- helpers
    def node(self, nid: str) -> dict:
        if nid not in self.d["nodes"]:
            raise GraphError("not_found", f"node {nid}")
        return self.d["nodes"][nid]

    def cur(self, nid: str) -> dict:
        return self.node(nid)["revisions"][-1]

    def kind(self, nid: str) -> str:
        return self.node(nid)["kind"]

    def state(self, nid: str) -> str:
        return self.cur(nid)["state"]

    def _event(self, cmd: str, detail: dict) -> int:
        self.d["revision"] += 1
        self.d["events"].append({"rev": self.d["revision"], "cmd": cmd, "ts": _now(), **detail})
        return self.d["revision"]

    def _new_id(self, kind: str) -> str:
        self.d["counter"] += 1
        return f"{PREFIX[kind]}-{self.d['counter']}"

    def _add_node(self, kind, title, payload, actor, cmd="AddNode") -> str:
        if kind not in KINDS:
            raise GraphError("invalid_argument", f"unknown kind {kind}")
        if kind == "origin" and any(n["kind"] == "origin" for n in self.d["nodes"].values()):
            raise GraphError("invalid_argument", "exactly one origin per graph")
        nid = self._new_id(kind)
        rev = self._event(cmd, {"node": nid, "kind": kind})
        self.d["nodes"][nid] = {"kind": kind, "created_rev": rev, "revisions": [
            {"rev": 1, "title": title, "state": INITIAL[kind], "payload": payload or {}, "actor": actor, "ts": _now()}]}
        return nid

    def edges(self, type_=None, src=None, dst=None):
        return [e for e in self.d["edges"] if (type_ is None or e["type"] == type_)
                and (src is None or e["src"] == src) and (dst is None or e["dst"] == dst)]

    # -- commands
    def add(self, kind: str, title: str, payload: dict | None = None, *, derives_from=(), supports=(),
            refutes=(), cites=(), produced_by=None, actor=DEFAULT_ACTOR) -> str:
        payload = dict(payload or {})
        if kind in EVIDENCE:
            payload.setdefault("epistemic", "inferred" if kind == "claim" else "observed")
            if payload["epistemic"] not in EPISTEMIC:
                raise GraphError("invalid_argument", f"epistemic must be one of {sorted(EPISTEMIC)}")
        if kind == "observation" and not (payload.get("quote") or payload.get("resource") or payload.get("evidence")):
            raise GraphError("invalid_argument", "observation needs inline evidence: quote, evidence, or resource")
        if kind == "claim" and not supports:
            raise GraphError("invalid_argument", "a claim must start with at least one supporting observation/claim")
        for s in list(supports) + list(refutes):
            self._require_active_evidence(s)
        if produced_by and self.state(produced_by) not in ("running", "open"):
            raise GraphError("stale_execution", f"{produced_by} is {self.state(produced_by)}; cannot produce output")
        nid = self._add_node(kind, title, payload, actor)
        if kind == "goal":
            origin = next(k for k, n in self.d["nodes"].items() if n["kind"] == "origin")
            self.link("spawns", origin, nid, _event=False)
        for p in derives_from:
            self.link("derives", p, nid, _event=False)
        if kind in FRONTIER_KINDS and not derives_from and kind != "goal":
            origin = next(k for k, n in self.d["nodes"].items() if n["kind"] == "origin")
            self.link("derives", origin, nid, _event=False)
        if produced_by:
            self.link("yields", produced_by, nid, _event=False)
        for s in supports:
            self.link("supports", s, nid, _event=False)
        for s in refutes:
            self.link("refutes", s, nid, _event=False)
        for s in cites:
            self.link("cites", nid, s, _event=False)
        return nid

    def _require_active_evidence(self, nid: str):
        if self.kind(nid) not in EVIDENCE:
            raise GraphError("invalid_relation", f"{nid} is a {self.kind(nid)}, not evidence")
        if self.state(nid) != "active":
            raise GraphError("stale_evidence", f"{nid} is retracted")

    def link(self, type_: str, src: str, dst: str, _event: bool = True, *, actor=DEFAULT_ACTOR) -> str:
        if type_ not in EDGES:
            raise GraphError("invalid_argument", f"unknown edge type {type_}")
        sk, dk, pinned = EDGES[type_]
        if src == dst:
            raise GraphError("invalid_relation", "self-link")
        if self.kind(src) not in sk or self.kind(dst) not in dk:
            raise GraphError("invalid_relation", f"{type_}: {self.kind(src)} -> {self.kind(dst)} not allowed")
        if type_ in ("supports", "refutes"):
            self._require_active_evidence(src)
            if self.kind(dst) == "goal" and self.state(dst) not in ("open", "needs_review"):
                raise GraphError("invalid_transition", f"goal {dst} is {self.state(dst)}; reopen before adding support")
        if any(e["type"] == type_ and e["src"] == src and e["dst"] == dst for e in self.d["edges"]):
            raise GraphError("already_exists", f"{type_} {src}->{dst}")
        if type_ in ("supports", "refutes") and self._reaches(dst, src, ("supports", "refutes")):
            raise GraphError("cycle_detected", f"{type_} {src}->{dst} would create an evidence cycle")
        if type_ == "depends_on":
            if self.state(src) != "open":
                raise GraphError("invalid_transition", "dependent work must be open")
            if self._reaches(dst, src, ("depends_on",)):
                raise GraphError("cycle_detected", "dependency cycle")
        eid = f"e{len(self.d['edges']) + 1}"
        e = {"id": eid, "type": type_, "src": src, "dst": dst}
        if pinned:
            e["src_rev"] = self.cur(src)["rev"]
            e["dst_rev"] = self.cur(dst)["rev"]
        rev = self._event("Link", {"edge": eid, "type": type_, "src": src, "dst": dst, "actor": actor}) if _event else self.d["revision"]
        e["created_rev"] = rev
        self.d["edges"].append(e)
        return eid

    def _reaches(self, start: str, target: str, types) -> bool:
        seen, stack = set(), [start]
        while stack:
            n = stack.pop()
            if n == target:
                return True
            if n in seen:
                continue
            seen.add(n)
            stack.extend(e["dst"] for e in self.d["edges"] if e["src"] == n and e["type"] in types)
        return False

    def revise(self, nid: str, payload: dict | None = None, title: str | None = None,
               expected_rev: int | None = None, actor=DEFAULT_ACTOR) -> int:
        n = self.node(nid)
        cur = n["revisions"][-1]
        if expected_rev is not None and expected_rev != cur["rev"]:
            raise GraphError("revision_conflict", f"{nid} is at rev {cur['rev']}, expected {expected_rev}")
        if n["kind"] == "origin":
            raise GraphError("invalid_transition", "origin is immutable")
        if cur["state"] in ("retracted", "consumed", "done"):
            raise GraphError("invalid_transition", f"cannot revise a {cur['state']} {n['kind']}")
        newp = {**cur["payload"], **(payload or {})}
        new = {"rev": cur["rev"] + 1, "title": title or cur["title"], "state": cur["state"], "payload": newp,
               "actor": actor, "ts": _now()}
        if n["kind"] == "goal" and cur["state"] == "met":
            new["state"] = "needs_review"
        n["revisions"].append(new)
        self._event("ReviseNode", {"node": nid, "node_rev": new["rev"]})
        self._invalidate_acceptances()
        return new["rev"]

    def retract(self, nid: str, reason: str, actor=DEFAULT_ACTOR) -> None:
        if self.kind(nid) not in EVIDENCE:
            raise GraphError("invalid_relation", "only observations/claims can be retracted")
        self._set_state(nid, "retracted", {"retract_reason": reason}, actor, "RetractEvidence")
        self._invalidate_acceptances()

    def set_state(self, nid: str, state: str, actor=DEFAULT_ACTOR, note: str = "") -> None:
        k = self.kind(nid)
        if state not in KINDS[k]:
            raise GraphError("invalid_argument", f"{k} states: {sorted(KINDS[k])}")
        cur = self.state(nid)
        if k == "work":
            if state not in WORK_TRANSITIONS[cur]:
                raise GraphError("invalid_transition", f"work {cur} -> {state}")
            if state == "running":
                for e in self.edges("depends_on", src=nid):
                    if self.state(e["dst"]) != "done":
                        raise GraphError("invalid_transition", f"prerequisite {e['dst']} not done")
        if k in EVIDENCE:
            raise GraphError("invalid_transition", "use retract for evidence")
        if k == "goal" and state == "met":
            raise GraphError("invalid_transition", "use accept to meet a goal")
        if k == "source" and state == "verified":
            raise GraphError("invalid_transition", "use verify-source with a verifier report")
        if k == "hypothesis" and state in ("supported", "refuted"):
            want = "supports" if state == "supported" else "refutes"
            if not [e for e in self.edges(want, dst=nid) if self.state(e["src"]) == "active"]:
                raise GraphError("stale_evidence", f"hypothesis cannot be {state} without an active {want} edge")
        self._set_state(nid, state, {"note": note} if note else {}, actor, "SetState")

    def verify_source(self, nid: str, verdict: str, report: str, actor=DEFAULT_ACTOR) -> None:
        if self.kind(nid) != "source":
            raise GraphError("invalid_relation", "not a source")
        states = {"verified": "verified", "rejected": "rejected", "unverified": "unverified",
                  "exists": "verified", "mismatch": "rejected", "not_found": "rejected",
                  "unverifiable": "unverified", "no_identifier": "unverified"}
        if verdict not in states:
            raise GraphError("invalid_argument", f"unknown verification verdict: {verdict}")
        state = states[verdict]
        self._set_state(nid, state, {"verification": {"verdict": verdict, "report": report}}, actor, "VerifySource")

    def _set_state(self, nid, state, extra, actor, cmd):
        n = self.node(nid)
        cur = n["revisions"][-1]
        n["revisions"].append({"rev": cur["rev"] + 1, "title": cur["title"], "state": state,
                               "payload": {**cur["payload"], **extra}, "actor": actor, "ts": _now()})
        self._event(cmd, {"node": nid, "state": state})

    def support_closure(self, edge_ids) -> tuple[list[str], list[str]]:
        """All support edges/evidence nodes reachable backwards from the selected direct edges."""
        by_id = {e["id"]: e for e in self.d["edges"]}
        edges, nodes, stack = [], [], list(edge_ids)
        while stack:
            eid = stack.pop()
            if eid in edges:
                continue
            e = by_id[eid]
            edges.append(eid)
            nodes.append(e["src"])
            stack.extend(x["id"] for x in self.d["edges"] if x["type"] == "supports" and x["dst"] == e["src"])
        return sorted(set(edges)), sorted(set(nodes))

    def accept(self, goal: str, edge_ids: list[str], rationale: str, method: str = "self",
               actor=DEFAULT_ACTOR) -> dict:
        if self.kind(goal) != "goal" or self.state(goal) not in ("open", "needs_review"):
            raise GraphError("invalid_transition", f"{goal} not an open goal")
        by_id = {e["id"]: e for e in self.d["edges"]}
        for eid in edge_ids:
            e = by_id.get(eid)
            if not e or e["type"] != "supports" or e["dst"] != goal:
                raise GraphError("invalid_argument", f"{eid} is not a direct support edge of {goal}")
        edges, nodes = self.support_closure(edge_ids)
        problems = self.stale_report(edges)
        if problems:
            raise GraphError("stale_evidence", "; ".join(problems))
        epi = {self.cur(n)["payload"].get("epistemic") for n in nodes}
        if epi <= {"reported"}:
            raise GraphError("stale_evidence", "closure rests only on reported (second-hand) evidence")
        acc = {"id": f"acc-{len(self.d['acceptances']) + 1}", "goal": goal, "goal_rev": self.cur(goal)["rev"],
               "edges": edges, "evidence": {n: self.cur(n)["rev"] for n in nodes}, "method": method,
               "rationale": rationale, "actor": actor, "stale": False, "ts": _now()}
        self.d["acceptances"].append(acc)
        self._set_state(goal, "met", {"acceptance": acc["id"]}, actor, "AcceptGoal")
        return acc

    def stale_report(self, edge_ids=None) -> list[str]:
        out = []
        for e in self.d["edges"]:
            if edge_ids is not None and e["id"] not in edge_ids:
                continue
            if e["type"] not in ("supports", "refutes", "cites", "answers"):
                continue
            if e["src"] not in self.d["nodes"] or e["dst"] not in self.d["nodes"]:
                continue  # reported as dangling by validate()
            if self.state(e["src"]) == "retracted":
                out.append(f"{e['id']} source {e['src']} retracted")
            elif e.get("src_rev") and e["src_rev"] != self.cur(e["src"])["rev"] and self.kind(e["src"]) in EVIDENCE:
                if self._content_rev(e["src"]) > e["src_rev"]:
                    out.append(f"{e['id']} pinned {e['src']}@{e['src_rev']} but current content rev {self._content_rev(e['src'])}")
        return out

    def _content_rev(self, nid):
        """Latest revision that changed title/payload content (state-only changes don't count)."""
        revs = self.node(nid)["revisions"]
        last = revs[0]["rev"]
        for a, b in zip(revs, revs[1:]):
            if a["title"] != b["title"] or {k: v for k, v in a["payload"].items() if k not in ("retract_reason", "note", "verification", "acceptance")} != \
                    {k: v for k, v in b["payload"].items() if k not in ("retract_reason", "note", "verification", "acceptance")}:
                last = b["rev"]
        return last

    def _invalidate_acceptances(self):
        for acc in self.d["acceptances"]:
            if acc["stale"]:
                continue
            bad = [n for n, r in acc["evidence"].items()
                   if self.state(n) == "retracted" or self._content_rev(n) > r]
            if bad:
                acc["stale"] = True
                acc["stale_reason"] = f"evidence changed: {', '.join(bad)}"
                if self.state(acc["goal"]) == "met":
                    self._set_state(acc["goal"], "needs_review", {"stale_acceptance": acc["id"]}, "host", "InvalidateAcceptance")

    # -- analysis
    def validate(self) -> dict:
        errors, warnings = [], []
        origins = [k for k, n in self.d["nodes"].items() if n["kind"] == "origin"]
        if len(origins) != 1:
            errors.append(f"expected exactly one origin, found {len(origins)}")
        for nid, n in self.d["nodes"].items():
            cur = n["revisions"][-1]
            if [r["rev"] for r in n["revisions"]] != list(range(1, len(n["revisions"]) + 1)):
                errors.append(f"{nid}: revision sequence has gaps")
            if cur["state"] not in KINDS[n["kind"]]:
                errors.append(f"{nid}: invalid state {cur['state']}")
            p = cur["payload"]
            if n["kind"] == "observation" and not (p.get("quote") or p.get("resource") or p.get("evidence")):
                errors.append(f"{nid}: observation without inline evidence")
            if n["kind"] in EVIDENCE and p.get("epistemic") not in EPISTEMIC:
                errors.append(f"{nid}: missing epistemic label")
            if n["kind"] == "claim" and not self.edges("supports", dst=nid):
                errors.append(f"{nid}: claim without support")
            if n["kind"] == "observation" and p.get("epistemic") == "observed" and p.get("quote") and not self.edges("cites", src=nid):
                warnings.append(f"{nid}: quoted observation does not cite a source node")
            if n["kind"] == "source" and cur["state"] == "verified" and not p.get("verification"):
                errors.append(f"{nid}: verified source without verifier report")
            if n["kind"] == "source" and cur["state"] == "unverified" and self.edges("cites", dst=nid):
                warnings.append(f"{nid}: cited source not yet verified")
            if n["kind"] in ("claim", "hypothesis") and cur["state"] in ("active", "supported", "open"):
                srcs = self._sources_behind(nid)
                if len(self.edges("supports", dst=nid)) and len(srcs) == 1:
                    warnings.append(f"{nid}: rests on a single source ({next(iter(srcs))})")
            if n["kind"] == "hypothesis" and self.edges("supports", dst=nid) and not self.edges("refutes", dst=nid) \
                    and not p.get("disconfirmation_sought"):
                warnings.append(f"{nid}: supported but no disconfirming evidence sought (set payload.disconfirmation_sought)")
        for e in self.d["edges"]:
            if e["src"] not in self.d["nodes"] or e["dst"] not in self.d["nodes"]:
                errors.append(f"{e['id']}: dangling endpoint")
                continue
            sk, dk, _ = EDGES[e["type"]]
            if self.kind(e["src"]) not in sk or self.kind(e["dst"]) not in dk:
                errors.append(f"{e['id']}: invalid endpoints for {e['type']}")
        for nid in self.d["nodes"]:
            if self._reaches_via(nid, ("supports", "refutes")):
                errors.append(f"evidence cycle through {nid}")
                break
        if any("dangling" in x for x in errors):
            return {"ok": False, "errors": errors, "warnings": warnings, "revision": self.d["revision"]}
        warnings += [f"stale: {s}" for s in self.stale_report()]
        for acc in self.d["acceptances"]:
            if acc["stale"]:
                warnings.append(f"{acc['id']} stale ({acc.get('stale_reason')}); goal {acc['goal']} needs review")
        return {"ok": not errors, "errors": errors, "warnings": warnings, "revision": self.d["revision"]}

    def _reaches_via(self, nid, types):
        return any(self._reaches(e["dst"], nid, types) for e in self.d["edges"] if e["src"] == nid and e["type"] in types)

    def _sources_behind(self, nid, depth=0) -> set:
        out = set()
        if depth > 32:
            return out
        for e in self.edges("cites", src=nid):
            out.add(e["dst"])
        for e in self.edges("supports", dst=nid):
            out |= self._sources_behind(e["src"], depth + 1)
        return out

    def _depth(self, nid) -> int:
        d, cur, seen = 0, nid, set()
        while True:
            parents = [e["src"] for e in self.d["edges"] if e["dst"] == cur and e["type"] in ("derives", "spawns")]
            if not parents or cur in seen:
                return d
            seen.add(cur)
            cur, d = parents[0], d + 1

    def frontier(self, limit: int = 10) -> list[dict]:
        out = []
        for nid, n in self.d["nodes"].items():
            if n["kind"] not in FRONTIER_KINDS:
                continue
            cur = n["revisions"][-1]
            if cur["state"] != "open":
                continue
            if any(self.state(e["dst"]) != "done" for e in self.edges("depends_on", src=nid)):
                continue
            p = cur["payload"]
            reasons, score = [], 0.0
            pr = float(p.get("priority", 1))
            score += 2 * pr
            reasons.append(f"priority {pr:g}")
            refs = p.get("refs") or p.get("rows")
            if refs:
                score += 1
                reasons.append(f"informs {','.join(refs)}")
            if n["kind"] == "hypothesis":
                sup = [e for e in self.edges("supports", dst=nid) if self.state(e["src"]) == "active"]
                ref = [e for e in self.edges("refutes", dst=nid) if self.state(e["src"]) == "active"]
                if not sup and not ref:
                    score += 1.5
                    reasons.append("untested")
                elif sup and not ref and not p.get("disconfirmation_sought"):
                    score += 1.0
                    reasons.append("seek disconfirmation")
                elif sup and ref:
                    score += 0.75
                    reasons.append("conflicting evidence")
            if n["kind"] == "question" and not self.edges("answers", dst=nid):
                score += 0.5
                reasons.append("unanswered")
            dep = self._depth(nid)
            score -= 0.25 * max(0, dep - 1)
            cost = float(p.get("cost", 1))
            score -= 0.5 * cost
            reasons.append(f"depth {dep}, cost {cost:g}")
            out.append({"id": nid, "kind": n["kind"], "title": cur["title"], "score": round(score, 3),
                        "reasons": reasons, "created_rev": n["created_rev"]})
        out.sort(key=lambda x: (-x["score"], x["created_rev"]))
        return out[:limit]

    def stop_check(self, max_work: int = 8, max_sources: int = 6, stagnation: int = 3,
                   elapsed_min: float = 0.0, max_minutes: float = 0.0) -> dict:
        goals = [k for k, n in self.d["nodes"].items() if n["kind"] == "goal"]
        live = [g for g in goals if self.state(g) not in ("abandoned",)]
        if live and all(self.state(g) == "met" for g in live):
            return {"stop": True, "reason": "all goals accepted"}
        done = [k for k, n in self.d["nodes"].items() if n["kind"] == "work" and self.state(k) == "done"]
        if len(done) >= max_work:
            return {"stop": True, "reason": f"work budget reached ({len(done)}/{max_work})"}
        nsrc = sum(1 for n in self.d["nodes"].values() if n["kind"] == "source")
        if nsrc >= max_sources:
            return {"stop": True, "reason": f"source budget reached ({nsrc}/{max_sources})"}
        if max_minutes and elapsed_min >= max_minutes:
            return {"stop": True, "reason": f"time budget reached ({elapsed_min:g}/{max_minutes:g} min)"}
        done_sorted = sorted(done, key=lambda k: self.node(k)["created_rev"])
        tail = done_sorted[-stagnation:] if stagnation else []
        if stagnation and len(tail) == stagnation and all(not self.edges("yields", src=w) for w in tail):
            return {"stop": True, "reason": f"stagnation: last {stagnation} work items yielded nothing"}
        if not self.frontier(1):
            return {"stop": True, "reason": "frontier empty"}
        return {"stop": False, "reason": "continue", "next": self.frontier(1)[0]["id"]}

    def render(self) -> str:
        def label_text(text: str) -> str:
            entities = str.maketrans({char: f"#{ord(char)};" for char in '"#<>&`'})
            return "<br/>".join(line.translate(entities) for line in textwrap.wrap(text, width=42))

        shape = {"origin": ("([", "])"), "goal": ("{{", "}}"), "question": ("[/", "/]"), "hypothesis": ("[\\", "\\]"),
                 "work": ("[", "]"), "source": ("[(", ")]"), "observation": ("(", ")"), "claim": ("[[", "]]"),
                 "hint": (">", "]")}
        lines = ["flowchart TD", "  classDef origin stroke-width:3px"]
        for nid, n in self.d["nodes"].items():
            cur = n["revisions"][-1]
            kind = n["kind"]
            a, b = shape[kind]
            safe = nid.replace("-", "_")
            title = cur["title"]
            detail = f"{kind.capitalize()} · {cur['state'].replace('_', ' ')}"
            if kind == "origin":
                title = cur["payload"].get("statement") or title
                detail = f"Origin · recorded by {cur['actor']}"
            label = f"{label_text(title)}<br/>{label_text(detail)}"
            lines.append(f'  {safe}{a}"{label}"{b}')
            if kind == "origin":
                lines.append(f"  class {safe} origin")
        stale = {s.split()[0] for s in self.stale_report()}
        for e in self.d["edges"]:
            arrow = "-.->" if e["id"] in stale or e["type"] == "depends_on" else "-->"
            label = e["type"].replace("_", " ")
            lines.append(f'  {e["src"].replace("-", "_")} {arrow}|{label}| {e["dst"].replace("-", "_")}')
        for nid, n in self.d["nodes"].items():
            if n["revisions"][-1]["state"] in ("retracted", "rejected", "refuted"):
                lines.append(f"  style {nid.replace('-', '_')} stroke-dasharray: 5 5")
        return "\n".join(lines) + "\n"

    def overview(self, limit: int = 8) -> dict:
        nodes = self.d["nodes"]
        counts: dict = {}
        for n in nodes.values():
            counts[n["kind"]] = counts.get(n["kind"], 0) + 1
        claims = [k for k, n in nodes.items() if n["kind"] == "claim"]
        return {
            "title": self.d["title"], "revision": self.d["revision"], "counts": counts,
            "goals": [{"id": k, "title": self.cur(k)["title"], "state": self.state(k)} for k, n in nodes.items() if n["kind"] == "goal"],
            "frontier": self.frontier(limit),
            "claims": [{"id": c, "title": self.cur(c)["title"], "state": self.state(c),
                        "epistemic": self.cur(c)["payload"].get("epistemic"),
                        "supports": len(self.edges("supports", dst=c)), "refutes": len(self.edges("refutes", dst=c))}
                       for c in claims[-limit:]],
            "omitted_claims": max(0, len(claims) - limit),
            "validation": self.validate(),
        }


# ------------------------------------------------------------------ CLI
def _ids(s):
    return [x for x in (s or "").split(",") if x]


def _payload(raw):
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise GraphError("invalid_argument", "payload must be a JSON object")
    return value


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("graph", type=Path)
    ap.add_argument("--actor", default=DEFAULT_ACTOR, help="author of this change")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init"); p.add_argument("--title", required=True); p.add_argument("--origin", required=True)
    p = sub.add_parser("add"); p.add_argument("kind", choices=sorted(set(KINDS) - {"origin"}))
    p.add_argument("--title", required=True); p.add_argument("--payload", default="{}")
    p.add_argument("--from", dest="derives", default=""); p.add_argument("--supports", default="")
    p.add_argument("--refutes", default=""); p.add_argument("--cites", default="")
    p.add_argument("--by", dest="produced_by"); p.add_argument("--priority", type=float)
    p.add_argument("--cost", type=float); p.add_argument("--rows", default="", help=argparse.SUPPRESS)
    p.add_argument("--refs", default="", help="comma-separated external record IDs")
    p.add_argument("--epistemic", choices=sorted(EPISTEMIC)); p.add_argument("--quote")
    p = sub.add_parser("link"); p.add_argument("type", choices=sorted(EDGES)); p.add_argument("src"); p.add_argument("dst")
    p = sub.add_parser("revise"); p.add_argument("node"); p.add_argument("--payload", default="{}")
    p.add_argument("--title"); p.add_argument("--expected-rev", type=int)
    p = sub.add_parser("retract"); p.add_argument("node"); p.add_argument("--reason", required=True)
    p = sub.add_parser("state"); p.add_argument("node"); p.add_argument("state"); p.add_argument("--note", default="")
    p = sub.add_parser("verify-source"); p.add_argument("node"); p.add_argument("verdict"); p.add_argument("--report", required=True)
    p = sub.add_parser("accept"); p.add_argument("goal"); p.add_argument("--edges", required=True)
    p.add_argument("--rationale", required=True); p.add_argument("--method", default="self")
    sub.add_parser("validate"); sub.add_parser("render")
    p = sub.add_parser("frontier"); p.add_argument("--limit", type=int, default=10)
    p = sub.add_parser("overview"); p.add_argument("--limit", type=int, default=8)
    p = sub.add_parser("stop-check"); p.add_argument("--max-work", type=int, default=8)
    p.add_argument("--max-sources", type=int, default=6); p.add_argument("--stagnation", type=int, default=3)
    p.add_argument("--elapsed-min", type=float, default=0.0); p.add_argument("--max-minutes", type=float, default=0.0)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "init":
            if a.graph.exists():
                raise GraphError("already_exists", str(a.graph))
            g = Graph.new(a.title, a.origin, actor=a.actor)
            g.save(a.graph)
            print(json.dumps({"ok": True, "revision": g.d["revision"]}))
            return 0
        g = Graph.load(a.graph)
        out: object
        if a.cmd == "add":
            payload = _payload(a.payload)
            for k in ("priority", "cost", "epistemic", "quote"):
                if getattr(a, k) is not None:
                    payload[k] = getattr(a, k)
            if a.refs:
                payload["refs"] = _ids(a.refs)
            if a.rows:
                payload["rows"] = _ids(a.rows)
            out = {"id": g.add(a.kind, a.title, payload, derives_from=_ids(a.derives), supports=_ids(a.supports),
                               refutes=_ids(a.refutes), cites=_ids(a.cites), produced_by=a.produced_by, actor=a.actor)}
        elif a.cmd == "link":
            out = {"edge": g.link(a.type, a.src, a.dst, actor=a.actor)}
        elif a.cmd == "revise":
            out = {"rev": g.revise(a.node, _payload(a.payload), a.title, a.expected_rev, actor=a.actor)}
        elif a.cmd == "retract":
            g.retract(a.node, a.reason, actor=a.actor); out = {"retracted": a.node}
        elif a.cmd == "state":
            g.set_state(a.node, a.state, note=a.note, actor=a.actor); out = {"node": a.node, "state": a.state}
        elif a.cmd == "verify-source":
            g.verify_source(a.node, a.verdict, a.report, actor=a.actor); out = {"node": a.node, "state": g.state(a.node)}
        elif a.cmd == "accept":
            out = g.accept(a.goal, _ids(a.edges), a.rationale, a.method, actor=a.actor)
        elif a.cmd == "validate":
            out = g.validate(); print(json.dumps(out, indent=1)); return 0 if out["ok"] else 1
        elif a.cmd == "render":
            sys.stdout.write(g.render()); return 0
        elif a.cmd == "frontier":
            print(json.dumps(g.frontier(a.limit), indent=1)); return 0
        elif a.cmd == "overview":
            print(json.dumps(g.overview(a.limit), indent=1)); return 0
        elif a.cmd == "stop-check":
            out = g.stop_check(a.max_work, a.max_sources, a.stagnation, a.elapsed_min, a.max_minutes)
            print(json.dumps(out)); return 0
        g.save(a.graph)
        print(json.dumps({"ok": True, "revision": g.d["revision"], "data": out}))
        return 0
    except (OSError, UnicodeError, json.JSONDecodeError) as e:
        print(json.dumps({"ok": False, "error": {"code": "io_error" if isinstance(e, OSError) else "invalid_json",
                                                  "message": str(e)}}))
        return 3
    except GraphError as e:
        print(json.dumps({"ok": False, "error": {"code": e.code, "message": str(e)}}))
        return 3


if __name__ == "__main__":
    sys.exit(main())
