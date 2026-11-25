---
inclusion: auto
name: exploration-memory
description: Use for investigations that span sessions or context compaction, or when resuming a decision or investigation saved in an exploration graph.
---

# Exploration memory

Use one graph for the task. Tools are in `.ns-egraph/tool/` and graph JSON files are in `.ns-egraph/graphs/`, relative to the project root. Choose the graph that fits the current request. A restored checkpoint is background from the session. Search by topic before creating a graph for a distinct investigation. For workflow details or command syntax, read the relevant section of `.ns-egraph/tool/cli.md`.

Record decisions, evidence references, blockers, and the next unresolved action as they arise. Keep raw transcripts, credentials, and private information out of graph records. Check recalled evidence before acting on it.

Subagents do not load this steering and do not fire the prompt hook. When you delegate a graph work item, run `python3 .ns-egraph/tool/egraph.py GRAPH_PATH brief NODE_ID`, paste its output into the subagent prompt, and give the subagent an evidence file path under `.scratch/` or `/tmp/`. The subagent writes findings as JSON lines in the brief's reply contract. Then you run `python3 .ns-egraph/tool/egraph.py GRAPH_PATH --actor SUBAGENT_NAME record EVIDENCE_FILE`. You are the only writer of a graph. Subagents must not run `add`, `link`, `state`, `revise`, or `checkpoint`, because concurrent writes can lose updates.

After a material change, save the next unresolved item using the current Kiro session ID. When Kiro supplies `KIRO_SESSION_ID` in the shell, use:

```sh
python3 .ns-egraph/tool/recall.py checkpoint .ns-egraph/graphs GRAPH_FILENAME --session "$KIRO_SESSION_ID" --next NODE_ID
```

If the variable is absent, pass the session ID explicitly when available. Kiro CLI can show it with `/session-id`. If the ID is unavailable, use a stable task name as `--session` and open it with `recall.py resume DIRECTORY --session NAME` when continuing. Use `--done` in place of `--next NODE_ID` when the task is complete.
