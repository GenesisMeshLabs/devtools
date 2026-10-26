"""Release-train tooling tests. Local bare repositories stand in for GitHub; no network calls."""

import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import release

ROOT = Path(__file__).resolve().parents[1]

# The shape of each component's declarations, as release.json expects them.
COMPONENT_FILES = {
    "sdk-typescript": {
        "VERSION": "1.8.1\n",
        "package.json": '{\n  "name": "genesis-mesh-sdk",\n  "version": "1.8.1",\n  "type": "module"\n}\n',
        "package-lock.json": ('{\n  "name": "genesis-mesh-sdk",\n  "version": "1.8.1",\n  "packages": {\n    "": {\n'
                              '      "name": "genesis-mesh-sdk",\n      "version": "1.8.1"\n    },\n'
                              '    "node_modules/x": {\n      "version": "1.8.1"\n    }\n  }\n}\n'),
    },
    "sdk-go": {"VERSION": "1.8.1\n"},
    "sdk-dotnet": {
        "VERSION": "1.8.1\n",
        "src/GenesisMesh.Sdk/GenesisMesh.Sdk.csproj": (
            "<Project>\n  <PropertyGroup>\n    <Version>1.8.1</Version>\n"
            "    <PackageValidationBaselineVersion>1.8.1</PackageValidationBaselineVersion>\n"
            "  </PropertyGroup>\n</Project>\n"),
    },
    "sdk-rust": {
        "VERSION": "1.8.1\n",
        "Cargo.toml": '[package]\nname = "genesis-mesh-sdk"\nversion = "1.8.1"\n\n[dependencies]\nserde = "1.8.1"\n',
        "edge/Cargo.toml": '[package]\nname = "genesis-mesh-edge"\n# The SDK\'s version.\nversion = "1.8.1"\n',
        "verifier/Cargo.toml": '[package]\nname = "genesis-mesh-verify"\n# The SDK\'s version.\nversion = "1.8.1"\n',
        "Cargo.lock": ''.join(f'[[package]]\nname = "{name}"\nversion = "1.8.1"\n\n'
                              for name in ("genesis-mesh-edge", "genesis-mesh-sdk", "genesis-mesh-verify", "serde")),
    },
    "gateway": {
        "Cargo.toml": '[package]\nname = "genesis-mesh-gateway"\nversion = "1.8.1"\n',
        "Cargo.lock": '[[package]]\nname = "genesis-mesh-gateway"\nversion = "1.8.1"\n\n[[package]]\nname = "redis"\nversion = "1.8.1"\n',
        "ui/openapi.json": '{\n  "info": {\n    "title": "Genesis Mesh Trust Gateway",\n    "version": "1.8.1"\n  }\n}\n',
    },
}

FAKE_CHANGELOG_SCRIPT = """import sys
from pathlib import Path
version, heading = sys.argv[2], sys.argv[4]
folder = Path("changelog.d") / version
body = "".join(p.read_text() for p in sorted(folder.glob("*.md")))
for p in folder.glob("*.md"):
    p.unlink()
folder.rmdir()
log = Path("CHANGELOG.md")
log.write_text(log.read_text().replace("# Changelog\\n", f"# Changelog\\n\\n{heading}\\n\\n{body}", 1))
"""


def write_tree(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        with (root / name).open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.manifest = release.load_release(ROOT / "release.json")
        self.entries = {entry["repo"]: entry for entry in self.manifest["train"]}

    def test_the_train_is_the_six_repositories_core_first(self):
        self.assertEqual([e["repo"] for e in self.manifest["train"]],
                         ["genesismesh", "sdk-typescript", "sdk-go", "sdk-dotnet", "sdk-rust", "gateway"])

    def test_every_component_declaration_is_rewritten_and_nothing_else(self):
        for repo, files in COMPONENT_FILES.items():
            with self.subTest(repo=repo), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                write_tree(root, files)
                entry = self.entries[repo]
                self.assertEqual(release.declared_version(root, entry), "1.8.1")

                release.write_sites(root, entry["sites"], "1.9.0")

                self.assertEqual(release.declared_version(root, entry), "1.9.0")
                text = "".join((root / name).read_text(encoding="utf-8") for name in files)
                # Dependencies and the .NET API baseline (moved by Renovate after NuGet publishes) keep their version.
                expected_left = {"sdk-typescript": 1, "sdk-dotnet": 1, "sdk-rust": 2, "gateway": 1}.get(repo, 0)
                self.assertEqual(text.count("1.8.1"), expected_left)

    def test_line_endings_are_kept(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_tree(root, {name: text.replace("\n", "\r\n") for name, text in COMPONENT_FILES["sdk-rust"].items()})
            release.write_sites(root, self.entries["sdk-rust"]["sites"], "1.9.0")
            data = (root / "Cargo.toml").read_bytes()
            self.assertIn(b'version = "1.9.0"\r\n', data)
            self.assertEqual(data.count(b"\n"), data.count(b"\r\n"))

    def test_a_moved_declaration_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_tree(root, {**COMPONENT_FILES["gateway"], "ui/openapi.json": '{"info": {"version": "1.8.1"}}\n'})
            with self.assertRaisesRegex(ValueError, "ui/openapi.json: expected 1 version declaration"):
                release.write_sites(root, self.entries["gateway"]["sites"], "1.9.0")
            self.assertIn('version = "1.8.1"', (root / "Cargo.toml").read_text(encoding="utf-8"))

    def test_a_component_without_fragments_gets_the_no_changes_entry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = release.ensure_changelog_entry(root, self.entries["sdk-go"], "1.9.0")
            self.assertEqual(path.read_text(encoding="utf-8"),
                             "### Changed\n\n- Released with Genesis Mesh 1.9.0. Nothing changes in this SDK.\n")
            self.assertIsNone(release.ensure_changelog_entry(root, self.entries["sdk-go"], "1.9.0"))
            # The core has no such entry: a core release without fragments is refused by changelog.py.
            self.assertIsNone(release.ensure_changelog_entry(Path(temp) / "core", self.entries["genesismesh"], "1.9.0"))

    def test_release_notes_are_the_core_changelog_section(self):
        changelog = "# Changelog\r\n\r\n## v1.9.0 - The Name\r\n\r\nIntro.\r\n\r\n### Added\r\n\r\n- A.\r\n\r\n## v1.8.1 - Old\r\n\r\n- B.\r\n"
        self.assertEqual(release.release_notes(changelog, "1.9.0"), ("The Name", "Intro.\n\n### Added\n\n- A.\n"))
        with self.assertRaisesRegex(ValueError, "merge the core release PR first"):
            release.release_notes(changelog, "2.0.0")

    def test_versions_must_be_release_versions(self):
        for bad in ("1.9", "v1.9.0", "1.9.0-rc1"):
            with self.assertRaises(ValueError):
                release.version_tuple(bad)

    def test_check_reports_each_checkout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for repo, files in COMPONENT_FILES.items():
                write_tree(root / repo, files)
            write_tree(root / "genesismesh", {"VERSION": "1.9.0\n"})
            output = []
            with patch("builtins.print", lambda *a, **k: output.append(" ".join(map(str, a)))):
                self.assertEqual(release.check(self.manifest, root), 1)
            self.assertIn("genesismesh     1.9.0", output)
            self.assertIn("gateway         1.8.1", output)
            self.assertTrue(output[-1].startswith("The checkouts declare different versions"))


class TrainTests(unittest.TestCase):
    """prepare and tag, end to end, against local bare repositories."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        environment = patch.dict(os.environ, {
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.org",
            "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.org",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.manifest = {
            "schema_version": 1, "organization": "GenesisMeshLabs", "core": "core",
            "train": [
                {"repo": "core", "sites": [{"file": "VERSION", "pattern": "\\A(\\d+\\.\\d+\\.\\d+)", "count": 1}],
                 "changelog_heading": "## v{version} - {title}", "no_change_entry": None, "fold": [],
                 "check": [], "tag_message": "Genesis Mesh {version} - {title}"},
                {"repo": "sdk-go", "sites": [{"file": "VERSION", "pattern": "\\A(\\d+\\.\\d+\\.\\d+)", "count": 1}],
                 "changelog_heading": "## [{version}] - {date}",
                 "no_change_entry": "Released with Genesis Mesh {version}. Nothing changes in this SDK.",
                 "fold": [], "check": [], "tag_message": "sdk-go {version}"},
            ],
        }
        for repo in ("core", "sdk-go"):
            work = self.root / "seed" / repo
            write_tree(work, {"VERSION": "1.8.1\n", "CHANGELOG.md": "# Changelog\n",
                              "scripts/changelog.py": FAKE_CHANGELOG_SCRIPT})
            if repo == "core":
                write_tree(work, {"changelog.d/1.9.0/feature.md": "### Added\n\n- A feature.\n"})
            self.git("init", "--quiet", "--initial-branch=main", cwd=work)
            self.git("add", "--all", cwd=work)
            self.git("commit", "--quiet", "-m", "start", cwd=work)
            self.git("clone", "--quiet", "--bare", str(work), str(self.root / f"{repo}.git"))
        self.remote = self.root.as_uri() + "/{repo}.git"
        self.calls = []

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    def github(self, *args):
        self.calls.append(args)
        return f"https://github.example/{args[0]}"

    def train(self, version="1.9.0"):
        self.runs = getattr(self, "runs", 0) + 1
        workdir = self.root / f"work-{self.runs}"
        workdir.mkdir()
        return release.Release(self.manifest, version, workdir, remote=self.remote, github=self.github, sign=False)

    def merge(self, repo, branch):
        self.git("update-ref", "refs/heads/main", f"refs/heads/{branch}", cwd=self.root / f"{repo}.git")

    def test_prepare_opens_a_release_pr_per_repository_then_tag_publishes(self):
        with patch("builtins.print"):
            opened = self.train().prepare("The Name", "2026-11-02", None, dry_run=False)
        self.assertEqual(len(opened), 2)
        core_pr, go_pr = [call for call in self.calls if call[:2] == ("pr", "create")]
        self.assertIn("GenesisMeshLabs/core", core_pr)
        self.assertIn("Merge this pull request first", core_pr[-1])
        self.assertIn("merge the core first", go_pr[-1])
        bare = self.root / "sdk-go.git"
        self.assertEqual(self.git("show", "release/1.9.0:VERSION", cwd=bare), "1.9.0")
        self.assertEqual(self.git("show", "release/1.9.0:CHANGELOG.md", cwd=bare),
                         "# Changelog\n\n## [1.9.0] - 2026-11-02\n\n### Changed\n\n- Released with Genesis Mesh 1.9.0. "
                         "Nothing changes in this SDK.")
        self.assertIn("## v1.9.0 - The Name", self.git("show", "release/1.9.0:CHANGELOG.md", cwd=self.root / "core.git"))

        # A second prepare refuses: the branch exists.
        with patch("builtins.print"), self.assertRaisesRegex(ValueError, "already has a release/1.9.0 branch"):
            self.train().prepare("The Name", "2026-11-02", None, dry_run=False)

        # Tagging waits for the merges.
        with patch("builtins.print"), self.assertRaisesRegex(ValueError, "merge its release/1.9.0 PR first"):
            self.train().tag(dry_run=False, confirm=lambda v: True)

        for repo in ("core", "sdk-go"):
            self.merge(repo, "release/1.9.0")
        with patch("builtins.print"):
            self.assertIsNone(self.train().tag(dry_run=True, confirm=lambda v: True))
            self.assertIsNone(self.train().tag(dry_run=False, confirm=lambda v: False))
        self.assertEqual(self.git("tag", "--list", cwd=bare), "")

        with patch("builtins.print"):
            url = self.train().tag(dry_run=False, confirm=lambda v: v == "1.9.0")
        self.assertEqual(url, "https://github.example/release")
        self.assertEqual(self.git("tag", "--list", "-n1", cwd=bare), "v1.9.0          sdk-go 1.9.0")
        self.assertEqual(self.git("tag", "--list", "-n1", cwd=self.root / "core.git"),
                         "v1.9.0          Genesis Mesh 1.9.0 - The Name")
        published = self.calls[-1]
        self.assertEqual(published[:3], ("release", "create", "v1.9.0"))
        self.assertIn("v1.9.0 - The Name", published)

        with patch("builtins.print"), self.assertRaisesRegex(ValueError, "already has v1.9.0"):
            self.train().tag(dry_run=False, confirm=lambda v: True)

    def test_prepare_refuses_an_older_version_and_a_core_release_without_title(self):
        with patch("builtins.print"):
            with self.assertRaisesRegex(ValueError, "needs --title"):
                self.train().prepare(None, "2026-11-02", None, dry_run=True)
            with self.assertRaisesRegex(ValueError, "already declares 1.8.1"):
                self.train("1.8.1").prepare("x", "2026-11-02", ["sdk-go"], dry_run=True)

    def test_dry_run_pushes_nothing(self):
        with patch("builtins.print"):
            self.assertEqual(self.train().prepare(None, "2026-11-02", ["sdk-go"], dry_run=True), [])
        self.assertEqual(self.git("branch", "--list", cwd=self.root / "sdk-go.git"), "* main")
        self.assertEqual(self.calls, [])

    def test_status_reads_prs_tags_and_runs(self):
        answers = {
            "pr": json.dumps([{"number": 7, "state": "MERGED"}]),
            "run": json.dumps([{"workflowName": "Publish", "status": "completed", "conclusion": "success"}]),
        }
        with patch.object(release, "remote_has", return_value=True):
            rows = release.status(self.manifest, "1.9.0", github=lambda *args: answers[args[0]])
        self.assertEqual(rows[1], "sdk-go          #7 merged      tagged      Publish: success")


class RenovatePresetTests(unittest.TestCase):
    """renovate/genesis-mesh.json: each custom manager finds the pins it is written for."""

    def setUp(self):
        self.preset = json.loads((ROOT / "renovate" / "genesis-mesh.json").read_text(encoding="utf-8"))
        self.managers = {m["description"].split(":")[0].split(" (")[0]: m for m in self.preset["customManagers"]}

    @staticmethod
    def python_regex(pattern):
        # Renovate's named groups are (?<name>...); Python writes (?P<name>...).
        return re.compile(re.sub(r"\(\?<([A-Za-z]\w*)>", r"(?P<\1>", pattern))

    def find(self, manager, path, text):
        files = [self.python_regex(p.strip("/")) for p in manager["managerFilePatterns"]]
        if not any(f.search(path) for f in files):
            return None
        match = self.python_regex(manager["matchStrings"][0]).search(text)
        return match and {k: v for k, v in match.groupdict().items() if v}

    def manager(self, prefix):
        return next(m for name, m in self.managers.items() if name.startswith(prefix))

    def test_only_genesis_mesh_packages_are_enabled(self):
        disable_all, genesis_mesh = self.preset["packageRules"]
        self.assertEqual((disable_all["matchPackageNames"], disable_all["enabled"]), (["*"], False))
        self.assertTrue(genesis_mesh["enabled"])
        self.assertEqual(genesis_mesh["groupName"], "Genesis Mesh")

    def test_pip_pins_in_docs_and_scripts(self):
        pip = self.manager("pip install")
        self.assertEqual(self.find(pip, "docs/local-authority.md", "pip install genesis-mesh==1.8.0\n"),
                         {"currentValue": "1.8.0"})
        self.assertEqual(self.find(pip, "tests/demo/scripts/setup.mjs", '  "genesis-mesh==1.8.0",'),
                         {"currentValue": "1.8.0"})
        self.assertIsNone(self.find(pip, "pyproject.toml", '"genesis-mesh==1.8.1"'))

    def test_rust_sdk_git_tag(self):
        cargo = self.manager("The Rust SDK")
        line = 'genesis-mesh-sdk = { git = "https://github.com/GenesisMeshLabs/sdk-rust", tag = "v1.8.1", default-features = false }'
        self.assertEqual(self.find(cargo, "crates/connect-core/Cargo.toml", line), {"currentValue": "v1.8.1"})

    def test_dotnet_msbuild_properties(self):
        msbuild = self.manager("A .NET SDK version")
        test = '<GmSdkDotnetVersion Condition="\'$(GmSdkDotnetVersion)\' == \'\'">1.7.0</GmSdkDotnetVersion>'
        self.assertEqual(self.find(msbuild, "tests/sdk-smoke-dotnet/sdk-smoke-dotnet.csproj", test), {"currentValue": "1.7.0"})
        baseline = "<PackageValidationBaselineVersion>1.7.0</PackageValidationBaselineVersion>"
        self.assertEqual(self.find(msbuild, "src/GenesisMesh.Sdk/GenesisMesh.Sdk.csproj", baseline), {"currentValue": "1.7.0"})
        self.assertIsNone(self.find(msbuild, "src/GenesisMesh.Sdk/GenesisMesh.Sdk.csproj", "<Version>1.8.1</Version>"))

    def test_generated_release_facts(self):
        facts = self.manager("The release a website")
        self.assertEqual(self.find(facts, "src/generated/release.json", '{\n  "version": "1.8.1",\n  "tests": 4000\n}'),
                         {"currentValue": "1.8.1"})

    def test_release_commits_move_with_their_tag(self):
        pins = self.manager("A release's commit")
        line = 'CORE_REVISION = "9e4d5c01e6ba7a22e73c17388796ec274f73a2fc"  # GenesisMeshLabs/genesismesh v1.8.1'
        self.assertEqual(self.find(pins, "scripts/home_pilot_lab.py", line), {
            "currentDigest": "9e4d5c01e6ba7a22e73c17388796ec274f73a2fc",
            "depName": "GenesisMeshLabs/genesismesh", "currentValue": "v1.8.1"})
        self.assertIn("GenesisMeshLabs/genesismesh", self.preset["packageRules"][1]["matchPackageNames"])

    def test_a_release_constant_with_its_commit_on_the_next_line(self):
        pins = self.manager("A release's commit")
        two_lines = self.python_regex(pins["matchStrings"][1])
        text = ('CORE_RELEASE = "v1.8.1"  # GenesisMeshLabs/genesismesh\n'
                'CORE_REVISION = "9e4d5c01e6ba7a22e73c17388796ec274f73a2fc"\n'
                'SDK_RELEASE = "v1.8.1"  # GenesisMeshLabs/sdk-rust\r\n'
                'SDK_REVISION = "e554ca482a3ef4bc1e7a2ef205ac1d537daa169d"\n')
        found = [m.groupdict() for m in two_lines.finditer(text)]
        self.assertEqual(found, [
            {"currentValue": "v1.8.1", "depName": "GenesisMeshLabs/genesismesh",
             "currentDigest": "9e4d5c01e6ba7a22e73c17388796ec274f73a2fc"},
            {"currentValue": "v1.8.1", "depName": "GenesisMeshLabs/sdk-rust",
             "currentDigest": "e554ca482a3ef4bc1e7a2ef205ac1d537daa169d"},
        ])


if __name__ == "__main__":
    unittest.main()
