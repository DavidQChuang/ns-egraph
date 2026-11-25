---
type: User Guide
title: CLI
description: Command reference and workflows for agents using ns-egraph.
sources:
  - resource: ./egraph.py
  - resource: ./recall.py
  - resource: ./integrations/hooks.py
---

# CLI

`ns-egraph` records the questions, evidence, decisions, and next actions of a bounded investigation. Use it when a task is likely to span sessions or when a decision needs traceable evidence. A self-contained task can proceed directly. [Project README](README.md)

`egraph.py` works on one graph. `recall.py` searches a directory of graphs and saves a session's checkpoint. Both run under Python 3.10 or newer. Semantic recall adds an optional local model. Run the help commands from the tool directory.

```sh
python3 egraph.py --help
python3 recall.py --help
```

## Paths and global option

```text
python3 egraph.py GRAPH [--actor NAME] COMMAND ...
python3 recall.py COMMAND DIRECTORY ...
```

Resolve paths from the shell's working directory. In an agent-host installation, the scripts and this guide are in `.ns-egraph/tool/` and the graph files are in `.ns-egraph/graphs/`. Run commands from the project root or use absolute paths. Search uses the JSON graph files directly inside `DIRECTORY`. Keep one writer per graph because concurrent writes can overwrite each other.

Pass `--actor NAME` before the graph command to attribute a mutation. The default is `user`. Put source text in graph records only when it is needed to interpret evidence. Prefer short excerpts and pointers to the underlying artifact. Keep credentials, private data, and raw transcripts out of graph memory.

| To do this | Use |
| --- | --- |
| Find existing work on the current topic | `recall DIRECTORY QUERY`, then `overview` on the matched graph. |
| Restore the exact next item for this session | `resume DIRECTORY [--session NAME]`. |
| Start a distinct investigation | `init`, then `add goal` and `add question`. |
| Choose what to investigate next | `frontier` and `stop-check`. |
| Update what the evidence supports | `add observation`, `add claim`, `link`, and `accept` once the evidence is sufficient. |
| Hand off unfinished work | `checkpoint DIRECTORY GRAPH_FILENAME --next NODE_ID`. |

Scope recall to the user's current request. An automatically restored checkpoint is background from the session. For example, when a user reopens a packing investigation to ask about a different topic, look up that topic on its own. A named request such as `Resume the packing delay review` can open a saved graph from a fresh session. The exact next item saved by a different session can be loaded with `resume --session NAME` when that session key is known.

## Run an investigation

This example uses fictional packing data and writes into a temporary directory. Run the block from the tool directory. The node IDs shown are specific to this new graph. In ongoing work, use `data.id` and `data.edge` from each command's JSON output.

```sh
memory_dir=$(mktemp -d)
graph="$memory_dir/packing.json"
eg() { python3 egraph.py "$graph" --actor packing-agent "$@"; }

eg init --title "Packing delay review" --origin "Where should we reduce packing delays?"
eg add goal --title "Choose one packing step to improve"
eg add question --title "Which step took the most time?" --from goal-2
eg add hypothesis --title "Label printing is the bottleneck" --from q-3
eg add work --title "Measure each packing step" --from h-4 --priority 3
eg frontier --limit 3
eg state w-5 running

printf 'Label printing took 9 of the 12 minutes.\n' > "$memory_dir/timing.txt"
eg add source --title "Sample timing sheet" --by w-5 \
  --payload '{"resource":"timing.txt"}'
eg add observation --title "Label printing took most of the time" --by w-5 --cites src-6 \
  --quote "Label printing took 9 of the 12 minutes."
eg state w-5 done
eg link answers obs-7 q-3
eg state q-3 answered
eg link supports obs-7 h-4
eg state h-4 supported
eg add claim --title "Try preprinting labels for the next packing run" --supports obs-7

support_edge=$(eg link supports c-8 goal-2 | python3 -c 'import json, sys; print(json.load(sys.stdin)["data"]["edge"])')
eg accept goal-2 --edges "$support_edge" --rationale "Printing took three quarters of the measured cycle."
eg add work --title "Measure the next run with preprinted labels" --from c-8
eg validate
eg overview
eg stop-check

python3 recall.py checkpoint "$memory_dir" packing.json --session demo --next w-9
python3 recall.py resume "$memory_dir" --session demo
```

The accepted goal records a decision about what to try. The follow-up measures the effect of the change. `validate` can report a warning for the unverified source while returning success. `stop-check` reports the goal as met even while the follow-up remains open. The checkpoint preserves that follow-up for the next turn.

## `init`

```text
egraph.py GRAPH init --title TITLE --origin REQUEST
```

Create a graph with an origin that records the request or starting context. The parent directory must already exist. An existing graph path is rejected. The origin is immutable. Use later questions or goals to refine the inquiry.

## `add`

```text
egraph.py GRAPH add KIND --title TITLE [--payload JSON_OBJECT]
  [--from IDS] [--by WORK_ID] [--supports IDS] [--refutes IDS] [--cites IDS]
  [--priority NUMBER] [--cost NUMBER] [--refs IDS]
  [--epistemic observed|inferred|reported] [--quote TEXT]
```

Use the relationship flags that fit the selected node kind. Multiple IDs are comma-separated without spaces. `--payload` takes a JSON object for metadata such as `aliases`, an evidence URL, or an artifact path. An alias is an exact phrase matched against future recall prompts. `--refs` takes external IDs such as issue numbers and also helps rank open work. `--priority` and `--cost` default to 1 for ranking.

| Kind | What to record |
| --- | --- |
| `goal` | A decision or outcome the graph should support. The origin automatically links to it. |
| `question` | An uncertainty to resolve. |
| `hypothesis` | A testable explanation or prediction. |
| `work` | A bounded action that can yield evidence. |
| `source` | A paper, file, measurement log, or other reference. |
| `observation` | Evidence directly inspectable through a `quote`, `resource`, or `evidence` field. Defaults to `observed`. |
| `claim` | An inference backed by one or more active observations or claims supplied with `--supports`. Defaults to `inferred`. |
| `hint` | A lead that can suggest a question or work item. |

Use `--from PARENT_ID` to derive a question, hypothesis, or work item. Without it, these nodes derive from the origin. Use `--by WORK_ID` when open or running work produces a source, observation, or claim. Use `--epistemic reported` for a second-hand report and preserve the distinction when presenting conclusions.

## `link`

```text
egraph.py GRAPH link TYPE SOURCE_ID TARGET_ID
```

Use the stored edge direction when supplying IDs. Recall can traverse the edge in either direction and shows that direction in the excerpt.

| Type | Stored direction |
| --- | --- |
| `spawns` | Origin → goal |
| `derives` | Origin, goal, question, hypothesis, observation, claim, or hint → question, hypothesis, or work |
| `yields` | Work → observation, claim, or source |
| `cites` | Observation or claim → source |
| `supports` | Observation or claim → claim, hypothesis, or goal |
| `refutes` | Observation or claim → claim or hypothesis |
| `answers` | Observation or claim → question |
| `depends_on` | Dependent work → prerequisite work |

For example, `link depends_on w-9 w-5` requires `w-5` to reach `done` before `w-9` can enter `running`. Add the dependency while the dependent is open. The CLI returns the edge ID under `data.edge`. Record revisions and retractions through the CLI to keep evidence history traceable.

## `state`

```text
egraph.py GRAPH state NODE_ID STATE [--note TEXT]
```

Record a workflow transition and use `--note` for a reason, such as a blocker. Open work can move to `running`, `stopped`, or `done`. Running work can move to `done`, `blocked`, `stopped`, or back to `open`. Reopen blocked or stopped work before running it again. Completed work is terminal.

| Kind | States |
| --- | --- |
| Goal | `open`, `met` through `accept`, `abandoned`, `needs_review` |
| Question | `open`, `answered`, `parked` |
| Hypothesis | `open`, `supported`, `refuted`, `inconclusive` |
| Work | `open`, `running`, `done`, `blocked`, `stopped` |
| Source | `unverified`, `verified` through `verify-source`, `rejected` |
| Observation or claim | `active`, `retracted` through `retract` |
| Hint | `active`, `consumed` |

A hypothesis needs an active incoming `supports` or `refutes` edge before taking the corresponding state. Record the edge that answers a question before marking it `answered` so another agent can trace the conclusion. A state change creates a new node revision.

## `revise` and `retract`

```text
egraph.py GRAPH revise NODE_ID [--title TITLE] [--payload JSON_OBJECT] [--expected-rev REV]
egraph.py GRAPH retract NODE_ID --reason TEXT
```

`revise` appends a node revision and shallow-merges supplied payload fields. When you read the current record as `obs-7@1`, pass `--expected-rev 1` to detect a changed node before revising it. This check compares node revisions. Graph-level revisions appear separately at the top of command output. Read current state and reassess after a conflict.

`retract` marks an observation or claim retracted and records why. Review any downstream decision after changing evidence. When an acceptance's recorded evidence is revised or retracted, the goal can move to `needs_review`. Run `validate` to identify stale support. Link a replacement observation or claim before accepting the goal again.

## `verify-source` and `accept`

```text
egraph.py GRAPH verify-source SOURCE_ID verified|rejected|unverified --report REFERENCE
egraph.py GRAPH accept GOAL_ID --edges EDGE_IDS --rationale TEXT [--method NAME]
```

`verify-source` records a verification result and a report reference. The verification itself happens outside the CLI. When a source is rejected, review observations that cite it and retract or replace evidence that no longer holds.

`accept` marks an open or review-required goal as `met`. Pass the IDs of direct `supports` edges ending at that goal, separated by commas. The command records those links and their supporting evidence revisions. It rejects stale support and an evidence chain whose nodes are all labeled `reported`. `--method` records how you reached the decision and defaults to `self`. Use the rationale to explain which conclusion the recorded evidence warrants.

## `frontier`, `overview`, `stop-check`, `validate`, and `render`

```text
egraph.py GRAPH frontier [--limit N]
egraph.py GRAPH overview [--limit N]
egraph.py GRAPH stop-check [--max-work N] [--max-sources N] [--stagnation N]
  [--elapsed-min MINUTES] [--max-minutes MINUTES]
egraph.py GRAPH validate
egraph.py GRAPH render
```

These commands read the graph. `frontier` ranks eligible open questions, hypotheses, and work. It gives the score and reasons, such as an unanswered question or competing evidence. The default limit is 10. `overview` reports node counts, goals, frontier, recent claims, and validation results. Its default limit is eight for the frontier and claims.

`stop-check` reports `stop` and a reason. It checks accepted goals, budgets, stagnation, and an empty frontier. Defaults are eight completed work items, six sources, and three recent completed work items that yielded nothing. A positive `--max-minutes` compares against the caller's `--elapsed-min`. Use the stop reason to distinguish a satisfied goal from an investigation that ran out of work or budget.

`validate` reports errors and warnings as JSON. Review warnings before relying on a result. `render` writes Mermaid to standard output for inspection or redirection.

```sh
python3 egraph.py examples/greenhouse.json overview --limit 4
python3 egraph.py examples/greenhouse.json render > /tmp/greenhouse.mmd
```

## `recall` and `index`

```text
recall.py recall DIRECTORY QUERY [--semantic]
recall.py index DIRECTORY [--download]
```

Give `recall` an exact reference, a query using meaningful terms from the request, or an explicit request to open a saved graph. A qualified node reference such as `greenhouse.json#obs-9@1` selects a node at its current revision. If the pinned revision has changed, use the graph's JSON to inspect its revision history. A prompt like `Show greenhouse.json` retrieves the highest-ranked open item or a goal. An ordinary filename mention can boost a node match.

Recall follows up to two graph links for context and returns up to three entries within 5,000 characters. Each record carries a graph name, node ID, revision, and status. Direct counterevidence has priority in the last entry. A source two hops away can displace a one-hop planning link. A miss produces no output. Treat a retrieved excerpt as a starting point and inspect the full graph when the excerpt leaves a consequential question open.

```sh
python3 recall.py recall examples "Did we rule out heat in the east bed?"
python3 recall.py recall examples "greenhouse.json#obs-9@1"
python3 recall.py recall examples "Show greenhouse.json"
```

`--semantic` tries the optional local model after a text miss. `index` builds or refreshes the cache for the directory. Use `--download` on an explicit setup run when the model files are needed, then refresh with `index DIRECTORY` after material graph changes. Recall uses cached vectors for current revisions. [Semantic setup](README.md#optional-semantic-recall)

## `checkpoint` and `resume`

```text
recall.py checkpoint DIRECTORY GRAPH_FILENAME [--session NAME] [--next NODE_ID | --done]
recall.py resume DIRECTORY [--session NAME]
```

Checkpoints store one graph pointer and an optional next item per session in that directory. Pass a graph filename relative to `DIRECTORY`. The next item must be `open`, `running`, `blocked`, or `needs_review`. A blocked item should explain what is needed to unblock it. Omitting `--next` allows `resume` to choose from the graph's current frontier. It also uses the frontier if the saved item is resolved.

Use one session key consistently. The default comes from `NS_EGRAPH_SESSION` when set and is otherwise `manual`. Claude Code's adapter supplies that variable. In Kiro, pass the available `KIRO_SESSION_ID` explicitly with `--session`, or choose a stable task name for manual resumption. Saving a checkpoint for another graph under the same key replaces that session's pointer. Use `--done` to close a finished checkpoint. Its graph remains searchable.

```sh
python3 recall.py checkpoint "$memory_dir" packing.json --session demo --next w-9
python3 recall.py resume "$memory_dir" --session demo
# After the follow-up is resolved
python3 recall.py checkpoint "$memory_dir" packing.json --session demo --done
```

## Exit codes and output

| Code | `egraph.py` | `recall.py` |
| --- | --- | --- |
| `0` | Command completed. `validate` can report warnings. Read the JSON from `stop-check` for the stop decision. | Command completed. `recall` and `resume` can return empty output. |
| `1` | `validate` reported graph errors. | Tool, path, index, or checkpoint error reported to standard error. |
| `2` | Invalid command-line arguments. | Invalid command-line arguments. |
| `3` | Graph command, input JSON, or file error. The error object includes a code and a message. | |

Successful graph mutations emit JSON with the graph's new `revision` and, where applicable, a `data` object carrying the node ID, edge ID, or result. Check those values before composing the next command.
