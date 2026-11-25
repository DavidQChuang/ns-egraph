"""Translate Claude Code hook events to bounded, read-only graph recall."""

import json
import os
import re
import sys
from pathlib import Path

from recall import recall, resume


def run(directory: Path) -> None:
    event = json.load(sys.stdin)
    name = event["hook_event_name"]
    session = event.get("session_id", "")
    if not isinstance(session, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session):
        return
    context = ""
    if name == "SessionStart":
        # Session IDs use a shell-safe alphabet, also accepted by PowerShell.
        env_file = os.getenv("CLAUDE_ENV_FILE")
        if env_file:
            with Path(env_file).open("a", encoding="utf-8") as stream:
                stream.write(f"\nexport NS_EGRAPH_SESSION={session}\n")
        if event.get("source") in {"resume", "compact"}:
            context = resume(directory, session)
    elif name == "UserPromptSubmit":
        prompt = event["prompt"]
        if re.fullmatch(r"(?:please\s+)?(?:resume|continue)(?:\s+(?:this|the)\s+task)?[.!?]?",
                        prompt.strip(), re.IGNORECASE):
            context = resume(directory, session)
        else:
            context = recall(directory, prompt)
    if context:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": name, "additionalContext": context}}))
