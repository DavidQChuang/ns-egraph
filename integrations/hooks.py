"""Shared input handling for hooks that add optional recall context."""

import argparse
import json
import re
import sys
from pathlib import Path

from egraph import GraphError
from recall import recall, resume


def prompt_context(directory: Path, prompt: str, session: str) -> str:
    if not isinstance(prompt, str) or not prompt.strip():
        return ""
    if re.fullmatch(r"(?:please\s+)?(?:resume|continue)(?:\s+(?:this|the)\s+task)?[.!?]?",
                    prompt.strip(), re.IGNORECASE):
        return resume(directory, session) if session else ""
    return recall(directory, prompt)


def run_hook(context, argv=None) -> int:
    parser = argparse.ArgumentParser(description="Emit relevant graph context for an agent hook.")
    parser.add_argument("directory", type=Path)
    args = parser.parse_args(argv)
    try:
        event = json.loads(sys.stdin.read().strip() or "{}")
        if isinstance(event, dict):
            text = context(args.directory, event)
            if text:
                print(text)
    except (OSError, ValueError, GraphError, KeyError, TypeError, AttributeError, IndexError):
        # Memory is optional. A bad event or checkpoint should not block a turn.
        pass
    return 0
