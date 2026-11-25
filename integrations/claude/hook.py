"""Translate Claude Code hook events to bounded graph recall."""

import json
import os
import re
from pathlib import Path

from integrations.hooks import prompt_context, run_hook
from recall import resume


def context(directory: Path, event: dict) -> str:
    name = event.get("hook_event_name")
    session = event.get("session_id", "")
    if not isinstance(session, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session):
        return ""
    text = ""
    if name == "SessionStart":
        # Session IDs use a shell-safe alphabet, also accepted by PowerShell.
        env_file = os.getenv("CLAUDE_ENV_FILE")
        if env_file:
            with Path(env_file).open("a", encoding="utf-8") as stream:
                stream.write(f"\nexport NS_EGRAPH_SESSION={session}\n")
        if event.get("source") in {"resume", "compact"}:
            text = resume(directory, session)
    elif name == "UserPromptSubmit":
        text = prompt_context(directory, event.get("prompt", ""), session)
    if not text:
        return ""
    return json.dumps({"hookSpecificOutput": {"hookEventName": name, "additionalContext": text}})


def main(argv=None) -> int:
    return run_hook(context, argv)


if __name__ == "__main__":
    raise SystemExit(main())
