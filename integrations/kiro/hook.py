"""Return graph context on stdout for Kiro command hooks."""

import os
from pathlib import Path

from integrations.hooks import prompt_context, run_hook
from recall import resume


def context(directory: Path, event: dict) -> str:
    name = event.get("hook_event_name", "").lower()
    session = event.get("session_id") or os.getenv("KIRO_SESSION_ID", "")
    if not isinstance(session, str):
        session = ""
    if name in {"sessionstart", "agentspawn", "postcompact"}:
        return resume(directory, session) if session else ""
    prompt = os.getenv("USER_PROMPT", event.get("prompt", event.get("user_prompt", "")))
    if name in {"userpromptsubmit", "promptsubmit"} or (not name and prompt):
        return prompt_context(directory, prompt, session)
    return ""


if __name__ == "__main__":
    raise SystemExit(run_hook(context))
