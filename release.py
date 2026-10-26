"""Cut a coordinated Genesis Mesh release across the release-train repositories.

    python release.py check                          # the version each checkout declares
    python release.py prepare 1.9.0 --title "Name"   # open release/1.9.0 in every train repository
    python release.py tag 1.9.0                      # sign and push the tags, publish the core release
    python release.py status 1.9.0                   # release PRs, tags, releases and publish runs

The core leads the train (genesismesh docs/development/versioning.md).
`prepare` writes the version wherever each repository declares it
(release.json), folds the changelog fragments, runs the repository's own
version check and opens one release pull request per repository. The
Maintainer reviews and merges them, the core first. `tag` then signs and
pushes vX.Y.Z in every repository, the core first, and publishes the core's
GitHub release, which starts the PyPI and image publication; the components
publish from their tags.

`prepare` and `tag` work in fresh clones in a temporary directory: your
checkouts are never touched. Commits and tags are signed with your Git signing
configuration, pushes use your Git credentials, and `gh` your GitHub login.
Repositories that consume a release (the MCP server, labs, sites) are updated
by Renovate pull requests (renovate/genesis-mesh.json), not by this script.
"""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Callable

import workspace

ROOT = Path(__file__).resolve().parent
NUMBER = re.compile(r"\d+\.\d+\.\d+")
REMOTE = "https://github.com/{organization}/{repo}.git"


def load_release(path: Path) -> dict:
    """Validate release.json: the train in release order, the core first."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("Expected release manifest schema_version 1")
    train = data.get("train")
    if not isinstance(train, list) or not train or train[0].get("repo") != data.get("core"):
        raise ValueError("The train must list the core first")
    names = [entry.get("repo") for entry in train]
    if len(set(names)) != len(names):
        raise ValueError("Duplicate repository in the train")
    for entry in train:
        repo = entry.get("repo")
        sites = entry.get("sites")
        if not isinstance(sites, list) or not sites:
            raise ValueError(f"{repo}: no version sites")
        for site in sites:
            if set(site) != {"file", "pattern", "count"} or re.compile(site["pattern"]).groups < 1:
                raise ValueError(f"{repo}: a site needs file, pattern (with a group) and count")
        for key in ("fold", "check"):
            if not isinstance(entry.get(key), list):
                raise ValueError(f"{repo}: {key} must be a list")
        if not isinstance(entry.get("tag_message"), str):
            raise ValueError(f"{repo}: no tag_message")
    return data


def version_tuple(version: str) -> tuple[int, int, int]:
    if not NUMBER.fullmatch(version):
        raise ValueError(f"Not a release version: {version!r}")
    major, minor, patch = (int(part) for part in version.split("."))
    return major, minor, patch


def _read(path: Path) -> str:
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def _write(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def site_versions(root: Path, sites: list[dict]) -> list[tuple[str, str]]:
    """(file, version) for every declaration; a count that no longer matches is an error."""
    found = []
    for site in sites:
        matches = list(re.finditer(site["pattern"], _read(root / site["file"])))
        if len(matches) != site["count"]:
            raise ValueError(f"{site['file']}: expected {site['count']} version declaration(s), found "
                             f"{len(matches)}; update release.json")
        found += [(site["file"], match.group(1)) for match in matches]
    return found


def declared_version(root: Path, entry: dict) -> str:
    versions = {version for _, version in site_versions(root, entry["sites"])}
    if len(versions) != 1:
        raise ValueError(f"{entry['repo']} declares several versions: {', '.join(sorted(versions))}")
    return versions.pop()


def write_sites(root: Path, sites: list[dict], version: str) -> None:
    def replace(match: re.Match[str]) -> str:
        start, end = match.span(1)
        return match.group(0)[: start - match.start()] + version + match.group(0)[end - match.start():]

    site_versions(root, sites)
    for name in dict.fromkeys(site["file"] for site in sites):
        text = _read(root / name)
        for site in sites:
            if site["file"] == name:
                text = re.sub(site["pattern"], replace, text)
        _write(root / name, text)


def ensure_changelog_entry(root: Path, entry: dict, version: str) -> Path | None:
    """A component with no fragment for the release gets the policy's "no changes" entry."""
    folder = root / "changelog.d" / version
    if folder.is_dir() and any(folder.glob("*.md")):
        return None
    if not entry.get("no_change_entry"):
        return None
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "release.md"
    _write(path, "### Changed\n\n- " + entry["no_change_entry"].format(version=version) + "\n")
    return path


def release_notes(changelog: str, version: str) -> tuple[str, str]:
    """The title and the body of the core's CHANGELOG section for ``version``."""
    lines = changelog.replace("\r\n", "\n").split("\n")
    heading = re.compile(rf"^## v{re.escape(version)} - (.+)$")
    for index, line in enumerate(lines):
        match = heading.match(line)
        if match:
            end = next((i for i in range(index + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
            return match.group(1).strip(), "\n".join(lines[index + 1:end]).strip() + "\n"
    raise ValueError(f"CHANGELOG.md has no '## v{version} - ...' section: merge the core release PR first")


def git(*args: str, cwd: Path | None = None, capture: bool = False) -> str:
    result = workspace.run(["git", *args], cwd, capture=capture)
    return result.stdout.strip() if capture else ""


def remote_has(url: str, ref: str) -> bool:
    result = subprocess.run(["git", "ls-remote", "--exit-code", url, ref], capture_output=True, text=True)
    if result.returncode not in (0, 2):
        raise ValueError(f"git ls-remote failed for {url}")
    return result.returncode == 0


def gh(*args: str) -> str:
    return workspace.run(["gh", *args], capture=True).stdout.strip()


def clone(url: str, path: Path) -> None:
    # The repository's own line endings: files are rewritten byte for byte.
    git("clone", "--quiet", "--filter=blob:none", "-c", "core.autocrlf=false", url, str(path))


def _fill(value, **values):
    if isinstance(value, list):
        return [_fill(item, **values) for item in value]
    return value.format(**values)


class Release:
    def __init__(self, manifest: dict, version: str, workdir: Path, *,
                 remote: str = REMOTE, github: Callable[..., str] = gh, sign: bool = True):
        version_tuple(version)
        self.manifest = manifest
        self.version = version
        self.workdir = workdir
        self.remote = remote
        self.github = github
        self.sign = sign
        self.organization = manifest["organization"]

    def url(self, repo: str) -> str:
        return self.remote.format(organization=self.organization, repo=repo)

    def entries(self, only: list[str] | None = None) -> list[dict]:
        train = self.manifest["train"]
        known = {entry["repo"] for entry in train}
        if only and any(name not in known for name in only):
            raise ValueError("Unknown repository; release.json lists the train")
        return [entry for entry in train if not only or entry["repo"] in only]

    def checkout(self, repo: str) -> Path:
        path = self.workdir / repo
        if not path.exists():
            clone(self.url(repo), path)
        return path

    def prepare(self, title: str | None, day: str, only: list[str] | None, dry_run: bool) -> list[str]:
        branch = f"release/{self.version}"
        entries = self.entries(only)
        if any(entry["repo"] == self.manifest["core"] for entry in entries) and not title:
            raise ValueError("The core release needs --title (its changelog heading and GitHub release)")
        opened = []
        for entry in entries:
            repo = entry["repo"]
            print(f"== {repo}")
            if remote_has(self.url(repo), f"refs/heads/{branch}"):
                raise ValueError(f"{repo} already has a {branch} branch")
            path = self.checkout(repo)
            current = declared_version(path, entry)
            if version_tuple(self.version) <= version_tuple(current):
                raise ValueError(f"{repo} already declares {current}")
            git("switch", "--quiet", "-c", branch, cwd=path)
            values = {"python": sys.executable, "version": self.version, "title": title or "", "date": day}
            if entry.get("set_version"):
                for command in _fill(entry["set_version"], **values):
                    workspace.run(command, path)
            else:
                write_sites(path, entry["sites"], self.version)
            if declared_version(path, entry) != self.version:
                raise ValueError(f"{repo}: the version was not written everywhere")
            if entry.get("changelog_heading"):
                if ensure_changelog_entry(path, entry, self.version):
                    print(f"  no changelog fragment: added the \"no changes\" entry")
                heading = entry["changelog_heading"].format(**values)
                workspace.run([sys.executable, "scripts/changelog.py", "release", self.version, "--heading", heading], path)
            for step in entry["fold"]:
                if (path / step["when"].format(**values)).exists():
                    workspace.run(_fill(step["command"], **values), path)
            for command in _fill(entry["check"], **values):
                workspace.run(command, path)
            git("add", "--all", cwd=path)
            git("commit", "--quiet", "-m", f"release: {self.version}", cwd=path)
            print(git("show", "--stat", "--format=", "HEAD", cwd=path, capture=True))
            if dry_run:
                continue
            git("push", "--quiet", "-u", "origin", branch, cwd=path)
            opened.append(self.github(
                "pr", "create", "-R", f"{self.organization}/{repo}", "--base", "main", "--head", branch,
                "--title", f"release: {self.version}", "--body", self.pr_body(entry, title)))
        return opened

    def pr_body(self, entry: dict, title: str | None) -> str:
        core = self.manifest["core"]
        name = f"Release {self.version}" + (f", {title}" if title else "")
        if entry["repo"] == core:
            return (f"{name}: the version everywhere it is stated (`scripts/check_version.py --set`), "
                    f"the changelog folded from `changelog.d/{self.version}/` and any upgrade notes.\n\n"
                    "Before merging, add the release narrative the ship skill lists (history, roadmap, "
                    "the plan's release gate). Merge this pull request first: the components' release "
                    "checks pass once the core declares the version on main.\n\n"
                    "Opened by `devtools/release.py prepare`.")
        changelog = f", and the changelog folded from `changelog.d/{self.version}/`" if entry.get("changelog_heading") else ""
        return (f"{name}: the version declarations{changelog}.\n\n"
                f"The release-train check passes once the core release PR ({self.organization}/{core}, "
                f"`release/{self.version}`) is on main; merge the core first.\n\n"
                "Opened by `devtools/release.py prepare`.")

    def tag(self, dry_run: bool, confirm: Callable[[str], bool]) -> str | None:
        tag = f"v{self.version}"
        entries = self.entries()
        for entry in entries:
            repo = entry["repo"]
            if remote_has(self.url(repo), f"refs/tags/{tag}"):
                raise ValueError(f"{repo} already has {tag}")
            declared = declared_version(self.checkout(repo), entry)
            if declared != self.version:
                raise ValueError(f"main of {repo} declares {declared}: merge its release/{self.version} PR first")
        core = self.workdir / self.manifest["core"]
        title, notes = release_notes(_read(core / "CHANGELOG.md"), self.version)
        print(f"Ready to tag {tag} in {', '.join(e['repo'] for e in entries)}, then publish "
              f"\"{tag} - {title}\" (the core's PyPI and image publication).")
        if dry_run or not confirm(self.version):
            return None
        for entry in entries:
            path = self.workdir / entry["repo"]
            message = entry["tag_message"].format(version=self.version, title=title)
            git("tag", "-s" if self.sign else "-a", tag, "-m", message, cwd=path)
            git("push", "--quiet", "origin", tag, cwd=path)
            print(f"  {entry['repo']}: {tag} pushed")
        notes_file = self.workdir / "release-notes.md"
        _write(notes_file, notes)
        return self.github("release", "create", tag, "-R", f"{self.organization}/{self.manifest['core']}",
                           "--verify-tag", "--title", f"{tag} - {title}", "--notes-file", str(notes_file))


def status(manifest: dict, version: str, github: Callable[..., str] = gh) -> list[str]:
    tag = f"v{version}"
    rows = []
    for entry in manifest["train"]:
        name = f"{manifest['organization']}/{entry['repo']}"
        prs = json.loads(github("pr", "list", "-R", name, "--head", f"release/{version}", "--state", "all",
                                "--json", "number,state", "--limit", "1") or "[]")
        pr = f"#{prs[0]['number']} {prs[0]['state'].lower()}" if prs else "no PR"
        tagged = "tagged" if remote_has(REMOTE.format(organization=manifest["organization"], repo=entry["repo"]),
                                        f"refs/tags/{tag}") else "not tagged"
        runs = json.loads(github("run", "list", "-R", name, "--branch", tag, "--limit", "20",
                                 "--json", "workflowName,status,conclusion") or "[]")
        published = ", ".join(f"{run['workflowName']}: {run['conclusion'] or run['status']}" for run in runs) or "no runs"
        rows.append(f"{entry['repo']:<15} {pr:<14} {tagged:<11} {published}")
    return rows


def check(manifest: dict, root: Path) -> int:
    versions = {}
    problems = 0
    for entry in manifest["train"]:
        path = root / entry["repo"]
        if not path.is_dir():
            print(f"{entry['repo']:<15} not cloned in {root}")
            continue
        try:
            versions[entry["repo"]] = declared_version(path, entry)
            print(f"{entry['repo']:<15} {versions[entry['repo']]}")
        except (OSError, ValueError) as error:
            problems += 1
            print(f"{entry['repo']:<15} {error}")
    if len(set(versions.values())) > 1:
        problems += 1
        print("The checkouts declare different versions (expected only while a release is under way).")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, default=ROOT / "release.json")
    parser.add_argument("--workspace", type=Path, default=ROOT.parent, help="Parent of repository checkouts")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="the version each checkout in the workspace declares")
    prepare = sub.add_parser("prepare", help="open release/<version> in every train repository")
    prepare.add_argument("version")
    prepare.add_argument("--title", help="the release name, e.g. 'The Vouching Horizon' (required with the core)")
    prepare.add_argument("--date", default=date.today().isoformat(), help="the changelog date (default: today)")
    prepare.add_argument("--repo", action="append", help="only this repository; repeat for several")
    prepare.add_argument("--dry-run", action="store_true", help="prepare and commit in the clones, push nothing")
    tag = sub.add_parser("tag", help="sign and push v<version> everywhere, then publish the core release")
    tag.add_argument("version")
    tag.add_argument("--dry-run", action="store_true", help="check that every main declares the version, tag nothing")
    tag.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    show = sub.add_parser("status", help="release PRs, tags and publish runs of a version")
    show.add_argument("version")
    args = parser.parse_args(argv)
    # Keep this script's lines in order with the output of the commands it runs.
    sys.stdout.reconfigure(line_buffering=True)
    try:
        manifest = load_release(args.manifest)
        if args.command == "check":
            return check(manifest, args.workspace)
        if args.command == "status":
            print("\n".join(status(manifest, args.version)))
            return 0
        with tempfile.TemporaryDirectory(prefix="gm-release-") as temp:
            release = Release(manifest, args.version, Path(temp))
            if args.command == "prepare":
                opened = release.prepare(args.title, args.date, args.repo, args.dry_run)
                print("\n".join(opened) if opened else "Dry run: nothing pushed.")
                return 0
            url = release.tag(args.dry_run, lambda v: args.yes or input(f"Type {v} to tag and publish: ").strip() == v)
            print(url or "Nothing tagged.")
            return 0
    except (OSError, ValueError) as error:
        print(f"release: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
