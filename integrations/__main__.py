"""Run an installed host hook using the graphs beside the tool directory."""

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", choices=("claude", "kiro"))
    parser.add_argument("--semantic", action="store_true")
    args = parser.parse_args()

    tool = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(tool))
    from integrations.hooks import run_hook

    if args.host == "claude":
        from integrations.claude.hook import context
    else:
        from integrations.kiro.hook import context

    hook_args = [str(tool.parent / "graphs")]
    if args.semantic:
        hook_args.append("--semantic")
    return run_hook(context, hook_args)


if __name__ == "__main__":
    raise SystemExit(main())
