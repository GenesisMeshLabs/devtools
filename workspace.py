"""Shared, standard-library-only workspace operations."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
TOOLS = {
    "git": ["git", "--version"],
    "python": [sys.executable, "--version"],
    "node": ["node", "--version"],
    "npm": ["npm", "--version"],
    "go": ["go", "version"],
    "rustc": ["rustc", "--version"],
    "cargo": ["cargo", "--version"],
    "dotnet": ["dotnet", "--version"],
}
BEGIN = "<!-- genesis-devtools:begin -->"
END = "<!-- genesis-devtools:end -->"


def load_manifest(path: Path) -> dict:
    """Validate repos.json and derive the repository list from its profiles.

    Every repository belongs to at least one profile. Profiles listed in
    "excluded_from_all" are skipped by --all. Only repositories with automated
    install or test commands need an entry under "recipes".
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 2:
        raise ValueError("Expected manifest schema_version 2")
    if data.get("organization") != "GenesisMeshLabs":
        raise ValueError("Manifest organization must be GenesisMeshLabs")
    profiles = data.get("profiles")
    if not isinstance(profiles, dict) or not profiles or data.get("default_profile") not in profiles:
        raise ValueError("Missing default profile")
    names: dict[str, None] = {}
    for profile, members in profiles.items():
        if not isinstance(members, list) or not members:
            raise ValueError(f"Invalid profile: {profile}")
        for name in members:
            if not isinstance(name, str) or not re.fullmatch(r"(?:[A-Za-z0-9][A-Za-z0-9_-]*|\.github)", name):
                raise ValueError(f"Invalid repository name in profile {profile}")
        if len(set(members)) != len(members):
            raise ValueError(f"Duplicate repository in profile {profile}")
        names.update(dict.fromkeys(members))
    skipped = data.get("excluded_from_all", [])
    if not isinstance(skipped, list) or any(p not in profiles or p == data["default_profile"] for p in skipped):
        raise ValueError("excluded_from_all must list non-default profile names")
    everyday = {n for p, members in profiles.items() if p not in skipped for n in members}
    recipes = data.get("recipes", {})
    if not isinstance(recipes, dict):
        raise ValueError("recipes must be an object keyed by repository name")
    for name in recipes:
        if name not in names:
            raise ValueError(f"Recipe for {name} has no profile")
    repositories = []
    for name in names:
        recipe = recipes.get(name, {})
        if not isinstance(recipe, dict) or any(k not in ("tools", "install", "test") for k in recipe):
            raise ValueError(f"Invalid recipe for {name}")
        tools = recipe.get("tools", {})
        if not isinstance(tools, dict) or any(k not in TOOLS for k in tools):
            raise ValueError(f"Unsupported tools for {name}")
        if any(v is not None and (not isinstance(v, str) or not re.fullmatch(r"\d+(?:\.\d+)*", v)) for v in tools.values()):
            raise ValueError(f"Invalid minimum tool version for {name}")
        for kind in ("install", "test"):
            commands = recipe.get(kind, [])
            if not isinstance(commands, list) or any(
                not isinstance(c, list) or not c or not all(isinstance(a, str) and a for a in c)
                for c in commands
            ):
                raise ValueError(f"Invalid {kind} commands for {name}")
        repositories.append({
            "name": name, "in_all": name in everyday, "tools": tools,
            "install": recipe.get("install", []), "test": recipe.get("test", []),
        })
    return {**data, "repositories": repositories}


def add_selection(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--manifest", type=Path, default=ROOT / "repos.json")
    parser.add_argument("--workspace", type=Path, default=ROOT.parent, help="Parent of repository checkouts")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--repo", action="append", help="Select a repository; repeat for several")
    group.add_argument("--profile", help="Select a named profile (default: core)")
    group.add_argument("--all", action="store_true", help="Select every repository except excluded profiles (extras)")


def selected(data: dict, args: argparse.Namespace) -> list[dict]:
    known = {r["name"]: r for r in data["repositories"]}
    if args.all:
        return [r for r in known.values() if r["in_all"]]
    if args.repo:
        names = args.repo
    else:
        profile = args.profile or data["default_profile"]
        if profile not in data["profiles"]:
            raise ValueError(f"Unknown profile: {profile}")
        names = data["profiles"][profile]
    if any(n not in known for n in names):
        raise ValueError("Unknown repository; inspect repos.json for supported names")
    return [known[n] for n in dict.fromkeys(names)]


def run(command: list[str], cwd: Path | None = None, *, capture: bool = False) -> subprocess.CompletedProcess:
    # Resolve npm.cmd and other Windows launchers as well as POSIX executables.
    executable = shutil.which(command[0])
    if executable is None:
        raise ValueError(f"Missing executable: {command[0]}")
    result = subprocess.run(
        [executable, *command[1:]], cwd=cwd, text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if result.returncode:
        # Avoid echoing credential-bearing remote URLs or environment values.
        raise ValueError(f"{Path(command[0]).name} failed (exit {result.returncode})")
    return result


def version_tuple(text: str) -> tuple[int, ...]:
    match = re.search(r"\d+(?:\.\d+)+", text)
    if match is None:
        raise ValueError("Could not parse tool version")
    return tuple(int(part) for part in match.group().split("."))


def check_tool(name: str, minimum: str | None) -> str:
    output = run(TOOLS[name], capture=True).stdout.strip()
    if minimum:
        actual = version_tuple(output)
        required = tuple(int(part) for part in minimum.split("."))
        width = max(len(actual), len(required))
        if actual + (0,) * (width - len(actual)) < required + (0,) * (width - len(required)):
            raise ValueError(f"{name} requires >= {minimum}; found {output}")
    return output


def remote_matches(url: str, name: str) -> bool:
    # Accept normal GitHub SSH/HTTPS remotes, including HTTPS usernames.
    match = re.fullmatch(r"(?:https://(?:[^/@]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+)/(.*?)(?:\.git)?/?", url.strip())
    return bool(match and match.group(1).lower() == "genesismeshlabs" and match.group(2).lower() == name.lower())


def inspect_checkout(path: Path, name: str) -> bool:
    if path.is_symlink():
        raise ValueError(f"Refusing symlink checkout: {path}")
    if not path.exists():
        return False
    if not path.is_dir() or not (path / ".git").exists():
        raise ValueError(f"Existing path is not a Git checkout: {path}")
    top = Path(run(["git", "rev-parse", "--show-toplevel"], path, capture=True).stdout.strip()).resolve()
    if top != path.resolve():
        raise ValueError(f"Path is not a repository root: {path}")
    origin = run(["git", "remote", "get-url", "origin"], path, capture=True).stdout.strip()
    if not remote_matches(origin, name):
        raise ValueError(f"Origin does not match GenesisMeshLabs/{name}; leaving checkout untouched")
    return True


def venv_python(path: Path) -> Path:
    return path / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def setup_commands(repo: dict, path: Path, kind: str = "install") -> list[list[str]]:
    replacements = {"{python}": sys.executable, "{venv_python}": str(venv_python(path))}
    return [[replacements.get(arg, arg) for arg in command] for command in repo.get(kind, [])]


def install_dependencies(repo: dict, path: Path, dry_run: bool) -> None:
    commands = setup_commands(repo, path)
    if not commands:
        print("  No automated dependency recipe; follow this repository's README.")
        return
    if (path / ".venv").is_symlink():
        raise ValueError(f"Refusing symlink virtual environment: {path / '.venv'}")
    if not dry_run:
        for tool, minimum in repo.get("tools", {}).items():
            check_tool(tool, minimum)
    for command in commands:
        # Preserve an existing virtual environment, including its interpreter.
        if command[1:] == ["-m", "venv", ".venv"] and (path / ".venv").exists():
            if not venv_python(path).exists():
                raise ValueError(f"Broken virtual environment at {path / '.venv'}; repair it explicitly")
            if not dry_run:
                output = run([str(venv_python(path)), "--version"], capture=True).stdout
                if version_tuple(output) < (3, 12):
                    raise ValueError("Existing virtual environment requires Python >= 3.12")
            continue
        print("  Run: " + json.dumps(command))
        if not dry_run:
            run(command, path)


def install_guidance(path: Path, dry_run: bool) -> None:
    if path.resolve() == ROOT:
        print("  Devtools AGENT.md already contains the shared guidance source.")
        return
    target = path / "AGENT.md"
    if target.is_symlink():
        raise ValueError(f"Refusing symlink instruction file: {target}")
    original = target.read_text(encoding="utf-8") if target.exists() else ""
    guidance = (ROOT / "AGENT.md").read_text(encoding="utf-8")
    shared_begin = "<!-- shared-guidance:begin -->"
    shared_end = "<!-- shared-guidance:end -->"
    if guidance.count(shared_begin) != 1 or guidance.count(shared_end) != 1 or guidance.index(shared_begin) > guidance.index(shared_end):
        raise ValueError("Root AGENT.md must contain one shared-guidance section")
    shared = guidance.split(shared_begin, 1)[1].split(shared_end, 1)[0].strip()
    block = f"{BEGIN}\n{shared}\n{END}"
    if BEGIN in original or END in original:
        if original.count(BEGIN) != 1 or original.count(END) != 1 or original.index(BEGIN) > original.index(END):
            raise ValueError(f"Malformed managed guidance block in {target}")
        start = original.index(BEGIN)
        end = original.index(END) + len(END)
        updated = original[:start] + block + original[end:]
    else:
        updated = original + ("\n\n" if original else "") + block + "\n"
    if original == updated:
        print("  AGENT.md guidance is current.")
    else:
        print("  Update AGENT.md shared guidance (preserve project instructions).")
        if not dry_run:
            target.write_text(updated, encoding="utf-8")


def configure_identity(path: Path, args: argparse.Namespace) -> None:
    for key, value in (
        ("user.name", args.git_name),
        ("user.email", args.git_email),
        ("credential.https://github.com.username", args.github_user),
    ):
        if value:
            print(f"  Repository Git setting: {key} = {value}")
            if not args.dry_run:
                run(["git", "config", "--local", key, value], path)


def pull_checkout(path: Path, dry_run: bool) -> None:
    # Only fast-forward the current branch; local work and diverged branches are left alone.
    if run(["git", "status", "--porcelain", "--untracked-files=no"], path, capture=True).stdout.strip():
        print("  Skip pull: uncommitted changes.")
        return
    try:
        upstream = run(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], path, capture=True).stdout.strip()
    except ValueError:
        print("  Skip pull: no upstream branch (or detached HEAD).")
        return
    print(f"  Pull {upstream} (fast-forward only)")
    if not dry_run:
        try:
            run(["git", "pull", "--ff-only", "--quiet"], path)
        except ValueError:
            raise ValueError(f"Could not fast-forward from {upstream}; the branch may have diverged") from None


def update_vscode_workspace(workspace: Path, names: list[str], dry_run: bool) -> None:
    # Add missing checkouts as folders; keep existing folders, settings, and order.
    target = workspace / f"{workspace.name}.code-workspace"
    if target.is_symlink():
        raise ValueError(f"Refusing symlink workspace file: {target}")
    data: dict = {"folders": []}
    if target.exists():
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raise ValueError(f"{target} is not plain JSON (comments?); add folders manually") from None
        if not isinstance(data, dict) or not isinstance(data.setdefault("folders", []), list):
            raise ValueError(f"Unexpected structure in {target}; add folders manually")
    existing = {f.get("path") for f in data["folders"] if isinstance(f, dict)}
    added = [name for name in names if name not in existing]
    if not added:
        print(f"VS Code workspace is current: {target}")
        return
    print(f"VS Code workspace: add {', '.join(added)} to {target}")
    if not dry_run:
        data["folders"].extend({"path": name} for name in added)
        target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
