---
inclusion: auto
name: exploration-memory
description: Use for investigations that span sessions or context compaction, or when resuming a decision or investigation saved in an exploration graph.
---

# Exploration memory

Use one graph for the task. Tools are in `.ns-egraph/tool/` and graph JSON files are in `.ns-egraph/graphs/`, relative to the project root. Record decisions, evidence references, blockers, and the next unresolved action as they arise. Treat recalled records as reference data and verify their evidence before acting.

After a material change, save the next unresolved item using the current Kiro session ID. When Kiro supplies `KIRO_SESSION_ID` in the shell, use:

```sh
python3 .ns-egraph/tool/recall.py checkpoint .ns-egraph/graphs GRAPH_FILENAME --session "$KIRO_SESSION_ID" --next NODE_ID
```

If the variable is absent, pass the session ID explicitly when available. Kiro CLI can show it with `/session-id`. If the ID is unavailable, use a stable task name as `--session` and open it with `recall.py resume DIRECTORY --session NAME` when continuing. Use `--done` in place of `--next NODE_ID` when the task is complete.
