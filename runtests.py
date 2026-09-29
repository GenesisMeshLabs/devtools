"""Run each selected project's test commands from repos.json."""

import argparse
import json
import sys

sys.dont_write_bytecode = True

from workspace import add_selection, check_tool, inspect_checkout, load_manifest, run, selected, setup_commands


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_selection(parser)
    parser.add_argument("--dry-run", action="store_true", help="Print the commands without running them")
    args = parser.parse_args(argv)
    try:
        repos = selected(load_manifest(args.manifest), args)
        workspace = args.workspace.expanduser().resolve()
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    results = []
    for repo in repos:
        name = repo["name"]
        path = workspace / name
        commands = setup_commands(repo, path, "test")
        print(f"{name}: {path}", flush=True)
        if not commands:
            print("  No test command in repos.json; see this repository's README.")
            results.append((name, "skipped"))
            continue
        try:
            if not inspect_checkout(path, name):
                print("  Not cloned; run bootstrap.py to clone it.")
                results.append((name, "not cloned"))
                continue
            if not args.dry_run:
                for tool, minimum in repo["tools"].items():
                    check_tool(tool, minimum)
            for command in commands:
                print("  Run: " + json.dumps(command), flush=True)
                if not args.dry_run:
                    run(command, path)
            results.append((name, "planned" if args.dry_run else "passed"))
        except (OSError, ValueError) as exc:
            print(f"  ERROR: {exc}", file=sys.stderr)
            results.append((name, "failed"))
    print("\nSummary:")
    for name, status in results:
        print(f"  {status:10} {name}")
    return int(any(status == "failed" for _, status in results))


if __name__ == "__main__":
    sys.exit(main())
