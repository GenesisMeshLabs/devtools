"""Read-only checks for selected repositories and their required toolchains."""

import argparse
import json
import shutil
import sys

sys.dont_write_bytecode = True

from workspace import add_selection, check_tool, inspect_checkout, load_manifest, run, selected, venv_python


def organization_drift(data):
    # Compare repos.json with the organization's live, non-archived repositories.
    output = run([
        "gh", "repo", "list", data["organization"], "--no-archived", "--limit", "1000", "--json", "name",
    ], capture=True).stdout
    live = {repo["name"] for repo in json.loads(output)}
    known = {repo["name"] for repo in data["repositories"]}
    problems = []
    if live - known:
        problems.append("missing from repos.json: " + ", ".join(sorted(live - known)))
    if known - live:
        problems.append("not found on GitHub (renamed, archived, or no access): " + ", ".join(sorted(known - live)))
    if problems:
        raise ValueError("; ".join(problems))
    return f"all {len(live)} repositories are listed in repos.json"


def diagnose(repos, workspace):
    checks = []

    def record(name, action):
        try:
            detail = action()
            checks.append({"check": name, "ok": True, "detail": detail})
        except (OSError, ValueError) as exc:
            checks.append({"check": name, "ok": False, "detail": str(exc)})

    record("git", lambda: check_tool("git", None))
    for repo in repos:
        name = repo["name"]
        path = workspace / name
        for tool, minimum in repo.get("tools", {}).items():
            record(f"{name}: {tool}", lambda t=tool, m=minimum: check_tool(t, m))

        def checkout():
            if not inspect_checkout(path, name):
                raise ValueError("Not cloned; run bootstrap.py for this selection")
            return str(path)

        record(f"{name}: checkout", checkout)
        if not checks[-1]["ok"]:
            continue

        def identity():
            author = run(["git", "config", "--get", "user.name"], path, capture=True).stdout.strip()
            email = run(["git", "config", "--get", "user.email"], path, capture=True).stdout.strip()
            if not author or not email:
                raise ValueError("Configure a commit name and email")
            return f"{author} <{email}> (commit identity, not push authentication)"

        record(f"{name}: identity", identity)
        record(f"{name}: working tree", lambda: "uncommitted changes" if run(
            ["git", "status", "--porcelain"], path, capture=True
        ).stdout.strip() else "clean")
        if any("{venv_python}" in command for command in repo["install"]):
            record(f"{name}: virtualenv", lambda: run(
                [str(venv_python(path)), "-m", "pip", "check"], path, capture=True
            ).stdout.strip())
    return checks


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_selection(parser)
    parser.add_argument("--json", action="store_true", help="Machine-readable results")
    parser.add_argument("--org", action="store_true", help="Also compare repos.json with GitHub (requires gh)")
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        repos = selected(manifest, args)
        checks = diagnose(repos, args.workspace.expanduser().resolve())
        if args.org:
            try:
                checks.append({"check": "organization", "ok": True, "detail": organization_drift(manifest)})
            except (OSError, ValueError) as exc:
                checks.append({"check": "organization", "ok": False, "detail": str(exc)})
    except (OSError, ValueError) as exc:
        checks = [{"check": "configuration", "ok": False, "detail": str(exc)}]
    ok = all(item["ok"] for item in checks)
    if args.json:
        print(json.dumps({"ok": ok, "checks": checks}, indent=2))
    else:
        for item in checks:
            print(f"{'OK' if item['ok'] else 'FAIL'} {item['check']}: {item['detail']}")
        print(f"GitHub CLI: {'installed' if shutil.which('gh') else 'optional, not installed'}")
        print("Authentication and non-Python dependency installation are not verified by doctor.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
