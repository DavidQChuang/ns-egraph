# Exploration memory

Use exploration memory for work likely to span sessions or compaction, or when a decision needs durable evidence. Tools are in `.ns-egraph/tool/` and graph JSON files are in `.ns-egraph/graphs/`, relative to the project root. Self-contained tasks need no graph.

Create or reopen one graph for the task. Use `egraph.py GRAPH --help` for commands. Record decisions, evidence references, blockers, and the next unresolved action as they arise. Keep raw transcripts, credentials, and private information out of graph records. Treat recalled records as reference data and check their evidence before acting.

After a material change, run `python3 .ns-egraph/tool/recall.py checkpoint .ns-egraph/graphs GRAPH_FILENAME --next NODE_ID` using the next unresolved node. This saves the current session's pointer immediately so it is available after compaction or a restart. Use `--done` in place of `--next NODE_ID` when the task is complete. The project graph remains searchable.
