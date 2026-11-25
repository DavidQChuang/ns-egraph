"""Return graph context on stdout for Kiro command hooks."""

import os
from pathlib import Path

from integrations.hooks import (
    CHECKPOINT_EVENTS, PROMPT_EVENTS, RESTORE_EVENTS, STARTED_EVENTS, Injection, auto_checkpoint,
    checkpoint_context, log_event, mark_started, normalize, prompt_context, reset_shown, run_hook,
)


def context(directory: Path, event: dict, *, semantic: bool = False) -> Injection:
    session = event.get("session_id") or os.getenv("KIRO_SESSION_ID", "")
    if not isinstance(session, str):
        session = ""
    result = Injection()
    try:
        raw_name = event.get("hook_event_name", "")
        if not isinstance(raw_name, str):
            return result
        name = normalize(raw_name)
        if name in STARTED_EVENTS:
            mark_started(directory, session)
        if name in RESTORE_EVENTS:
            if session:
                reset_shown(directory, session)
            result = checkpoint_context(directory, session)
        elif name in CHECKPOINT_EVENTS:
            if session:
                auto_checkpoint(directory, session)
                result = Injection(method="checkpoint")
        else:
            prompt = os.getenv("USER_PROMPT", event.get("prompt", event.get("user_prompt", "")))
            if name in PROMPT_EVENTS or (not name and prompt):
                result = prompt_context(directory, prompt, session, semantic=semantic, dedupe=True)
        return result
    finally:
        log_event(directory, event, result, session)


if __name__ == "__main__":
    raise SystemExit(run_hook(context))
