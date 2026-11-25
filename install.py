#!/usr/bin/env python3
"""Install or remove ns-egraph hooks in a Claude Code or Kiro project."""

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
HOME_DIR = Path(".ns-egraph")
TOOL = HOME_DIR / "tool"
KIRO_PROFILE_DIR = Path(".kiro/agents")
STEERING = {
    "claude": (Path(".claude/rules/ns-egraph.md"), "integrations/claude/rule.md"),
    "kiro": (Path(".kiro/steering/ns-egraph.md"), "integrations/kiro/steering.md"),
}
CLAUDE_SETTINGS = Path(".claude/settings.local.json")
KIRO_HOOKS = Path(".kiro/hooks/ns-egraph.json")
KIRO_RESOURCE = "file://.kiro/steering/ns-egraph.md"
OLD_COMMANDS = {
    "claude": (
        'cd "$CLAUDE_PROJECT_DIR/.ns-egraph/tool" && python3 -B -m integrations.claude.hook ../graphs',
        'python3 -B "$CLAUDE_PROJECT_DIR/.ns-egraph/tool/recall.py" claude-hook "$CLAUDE_PROJECT_DIR/.ns-egraph/graphs"',
    ),
    "kiro": ("cd ./.ns-egraph/tool && python3 -B -m integrations.kiro.hook ../graphs",),
}


def project_file(root: Path, relative: Path) -> Path:
    path = root / relative
    if not path.is_relative_to(root) or path.resolve() != path:
        raise ValueError(f"Expected a local project path without symlinks: {path}")
    return path


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def write_file(path: Path, data: bytes | None) -> None:
    if data is None:
        path.unlink(missing_ok=True)
        return
    if path.exists() and path.read_bytes() == data:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        if path.exists():
            os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def semantic_python() -> Path:
    venv_python = ".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python"
    return TOOL / venv_python


def new_command(host: str, semantic: bool) -> str:
    prefix = "${CLAUDE_PROJECT_DIR}/" if host == "claude" else ""
    interpreter = f'"{prefix}{semantic_python().as_posix()}"' if semantic else "python3"
    launcher = (TOOL / "integrations").as_posix()
    command = f'{interpreter} -B "{prefix}{launcher}" {host}'
    return command + " --semantic" if semantic else command


def recognized_commands(host: str) -> set[str]:
    standard = new_command(host, False)
    commands = {standard, new_command(host, True)}
    # Recognize copies of the previously documented shell commands.
    for command in OLD_COMMANDS[host]:
        commands.update((command, command + " --semantic"))
        commands.add(command.replace("python3 -B", ".venv/bin/python -B") + " --semantic")
    return commands


def replace_actions(actions: list, owned: set[str]) -> list:
    return [action for action in actions if action.get("command") not in owned]


def update_events(settings: dict, events: set[str], host: str, command: str | None) -> None:
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError(f"Expected an event map in {host} settings")
    owned = recognized_commands(host)
    for event in sorted(events):
        existing = hooks.get(event, [])
        if not isinstance(existing, list):
            raise ValueError(f"Expected a list for the {event} hooks")
        if host == "claude":
            items = []
            for group in existing:
                if "hooks" not in group:
                    items.append(group)
                    continue
                actions = replace_actions(group["hooks"], owned)
                if actions or actions == group["hooks"]:
                    items.append({**group, "hooks": actions})
            if command:
                items.append({"hooks": [{"type": "command", "command": command, "timeout": 5}]})
        else:
            items = replace_actions(existing, owned)
            if command:
                items.append({"command": command})
        if items:
            hooks[event] = items
        elif existing:
            hooks.pop(event)
    if hooks:
        settings["hooks"] = hooks
    else:
        settings.pop("hooks", None)


def kiro_hooks(command: str) -> dict:
    settings = load_json(SOURCE / "integrations/kiro/hooks.json")
    for hook in settings["hooks"]:
        hook["action"]["command"] = command
    return settings


def update_kiro_profile(settings: dict, command: str | None) -> None:
    hooks = settings.get("hooks")
    if isinstance(hooks, list):
        owned = recognized_commands("kiro")
        hooks = [hook for hook in hooks if hook.get("action", {}).get("command") not in owned]
        if command:
            hooks.extend(kiro_hooks(command)["hooks"])
        settings["hooks"] = hooks
    else:
        update_events(settings, {"agentSpawn", "userPromptSubmit"}, "kiro", command)

    resources = settings.get("resources", [])
    if not isinstance(resources, list):
        raise ValueError("Expected a list for the Kiro profile resources")
    if command and KIRO_RESOURCE not in resources:
        settings["resources"] = resources + [KIRO_RESOURCE]
    elif not command and KIRO_RESOURCE in resources:
        resources.remove(KIRO_RESOURCE)
        if not resources:
            settings.pop("resources")


def owned_file(path: Path, desired: bytes | None, previous_digest: str | None) -> None:
    if path.exists():
        current = path.read_bytes()
        if current != desired and hashlib.sha256(current).hexdigest() != previous_digest:
            raise ValueError(f"File has local edits or belongs to another install: {path}")


def apply(root: Path, host: str, *, install: bool, semantic: bool, kiro_agent: Path | None) -> bool:
    receipt_path = project_file(root, HOME_DIR / f"installed-{host}.json")
    receipt = load_json(receipt_path)
    if not install and not receipt_path.exists():
        return False

    profile = Path(receipt["kiro_agent"]) if receipt.get("kiro_agent") else None
    if kiro_agent is not None:
        candidate = project_file(root, kiro_agent)
        if candidate.parent != root / KIRO_PROFILE_DIR or candidate.suffix != ".json" or not candidate.is_file():
            raise ValueError("--kiro-agent must select an existing JSON file in .kiro/agents/")
        chosen = candidate.relative_to(root)
        if receipt_path.exists() and profile != chosen:
            raise ValueError("Remove the current Kiro installation before selecting a different agent")
        profile = chosen
    if profile:
        if profile.parent != KIRO_PROFILE_DIR or profile.suffix != ".json":
            raise ValueError("The recorded Kiro profile must be in .kiro/agents/")
        receipt["kiro_agent"] = profile.as_posix()

    command = new_command(host, semantic) if install else None
    # Validate every destination before changing host settings.
    if install and semantic and not (root / semantic_python()).is_file():
        raise ValueError(f"Create a virtual environment at {root / TOOL / '.venv'} before using --semantic")
    rule, template = STEERING[host]
    managed = {rule: (SOURCE / template).read_bytes() if install else None}
    settings_path = None
    settings = None
    original_settings = None
    if host == "claude":
        settings_path = project_file(root, CLAUDE_SETTINGS)
        if install and not settings_path.exists():
            receipt["created_settings"] = True
        settings = load_json(settings_path)
        original_settings = json_bytes(settings)
        update_events(settings, {"SessionStart", "UserPromptSubmit"}, host, command)
    elif profile:
        settings_path = project_file(root, profile)
        if settings_path.exists() or install:
            if not settings_path.is_file():
                raise ValueError(f"Kiro agent profile is missing: {settings_path}")
            settings = load_json(settings_path)
            original_settings = json_bytes(settings)
            update_kiro_profile(settings, command)
    else:
        managed[KIRO_HOOKS] = json_bytes(kiro_hooks(command)) if command else None

    files = receipt.get("files", {})
    for relative, data in managed.items():
        path = project_file(root, relative)
        previous = files.get(relative.as_posix())
        if install and relative == KIRO_HOOKS and not previous and path.is_file():
            existing = load_json(path)
            if any(existing == kiro_hooks(command) for command in recognized_commands(host)):
                previous = hashlib.sha256(path.read_bytes()).hexdigest()
        owned_file(path, data, previous)

    if install:
        scripts = ["install.py", "egraph.py", "recall.py", "semantic_recall.py", "requirements-semantic.txt",
                   "README.md", "cli.md"]
        templates = [item.relative_to(SOURCE) for item in (SOURCE / "integrations").rglob("*")
                     if item.is_file() and item.suffix in {".py", ".json", ".md"}]
        for relative in [*map(Path, scripts), *templates]:
            write_file(project_file(root, TOOL / relative), (SOURCE / relative).read_bytes())
        project_file(root, HOME_DIR / "graphs").mkdir(parents=True, exist_ok=True)
        ignore = project_file(root, HOME_DIR / ".gitignore")
        if not ignore.exists():
            write_file(ignore, b"*\n")

    new_settings = json_bytes(settings) if settings is not None else None
    if settings_path is not None and new_settings != original_settings:
        if json_bytes(load_json(settings_path)) != original_settings:
            raise ValueError(f"Settings changed during installation or removal: {settings_path}")

    for relative, data in managed.items():
        write_file(project_file(root, relative), data)
    if install:
        receipt["files"] = {path.as_posix(): hashlib.sha256(data).hexdigest()
                            for path, data in managed.items() if data is not None}
        write_file(receipt_path, json_bytes(receipt))
    if settings_path is not None and new_settings != original_settings:
        data = None if not install and not settings and receipt.get("created_settings") else new_settings
        write_file(settings_path, data)
    if not install:
        write_file(receipt_path, None)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("install", "remove"):
        sub = commands.add_parser(action)
        sub.add_argument("host", choices=tuple(STEERING))
        sub.add_argument("--project", type=Path, default=Path.cwd(), help="project directory (default: current directory)")
        if action == "install":
            sub.add_argument("--semantic", action="store_true", help="use the installed tool's virtual environment for recall")
            sub.add_argument("--kiro-agent", type=Path, help="merge into one existing project Kiro agent profile")
    args = parser.parse_args()
    kiro_agent = getattr(args, "kiro_agent", None)
    if kiro_agent is not None and args.host != "kiro":
        parser.error("--kiro-agent requires host kiro")
    try:
        root = args.project.expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError(f"Expected a project directory: {root}")
        changed = apply(root, args.host, install=args.action == "install",
                        semantic=getattr(args, "semantic", False), kiro_agent=kiro_agent)
    except (OSError, ValueError, AttributeError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if changed:
        action = "Installed" if args.action == "install" else "Removed"
        print(f"{action} ns-egraph hooks and guidance for {args.host} in {root}")
    else:
        print(f"No managed {args.host} installation in {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
