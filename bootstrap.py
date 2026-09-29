"""Clone selected projects and optionally prepare their local environments."""

import argparse
import sys

sys.dont_write_bytecode = True

from workspace import (
    add_selection, check_tool, configure_identity, inspect_checkout,
    install_dependencies, install_guidance, load_manifest, pull_checkout, run, selected,
    update_vscode_workspace,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_selection(parser)
    parser.add_argument("--dry-run", action="store_true", help="Print planned changes without writing or installing")
    parser.add_argument("--pull", action="store_true", help="Fast-forward existing clean checkouts from their upstream")
    parser.add_argument("--install", action="store_true", help="Install project dependencies using manifest recipes")
    parser.add_argument("--vscode", action="store_true", help="Add cloned projects to <workspace>/<name>.code-workspace")
    parser.add_argument("--agents", action="store_true", help="Add/update shared guidance in each AGENT.md")
    parser.add_argument("--git-name", help="Set repository-local commit author name")
    parser.add_argument("--git-email", help="Set repository-local commit email")
    parser.add_argument("--github-user", help="Set HTTPS credential username (does not log in)")
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        repos = selected(manifest, args)
        check_tool("git", None)
        workspace = args.workspace.expanduser().resolve()
        if args.github_user and not all(c.isalnum() or c == "-" for c in args.github_user):
            raise ValueError("GitHub username must contain only letters, numbers, or hyphens")
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    errors = 0
    for repo in repos:
        name = repo["name"]
        path = workspace / name
        print(f"{name}: {path}", flush=True)
        try:
            if inspect_checkout(path, name):
                if args.pull:
                    pull_checkout(path, args.dry_run)
                else:
                    print("  Existing checkout preserved (no pull, reset, or branch changes).")
            else:
                user = f"{args.github_user}@" if args.github_user else ""
                url = f"https://{user}github.com/GenesisMeshLabs/{name}.git"
                print(f"  Clone {url}")
                if not args.dry_run:
                    workspace.mkdir(parents=True, exist_ok=True)
                    run(["git", "clone", "--", url, str(path)])
            configure_identity(path, args)
            if args.agents:
                install_guidance(path, args.dry_run)
            if args.install:
                install_dependencies(repo, path, args.dry_run)
        except (OSError, ValueError) as exc:
            print(f"  ERROR: {exc}", file=sys.stderr)
            errors += 1
    if args.vscode:
        # Include every cloned project, not just this selection. A dry run also
        # lists the projects it would have cloned.
        planned = {r["name"] for r in repos} if args.dry_run else set()
        names = [r["name"] for r in manifest["repositories"] if (workspace / r["name"] / ".git").exists() or r["name"] in planned]
        try:
            update_vscode_workspace(workspace, names, args.dry_run)
        except (OSError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            errors += 1
    print(f"{'Preview' if args.dry_run else 'Setup'} complete: {len(repos)} selected, {errors} failed.")
    return int(errors > 0)


if __name__ == "__main__":
    sys.exit(main())
