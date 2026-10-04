"""Workspace regression tests. No network calls or dependency installation."""

import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import bootstrap
import doctor
import runtests
import workspace


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        environment = patch.dict(os.environ, {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.manifest = workspace.load_manifest(workspace.ROOT / "repos.json")

    def git(self, repo, *args):
        return workspace.run(["git", *args], repo, capture=True).stdout.strip()

    def checkout(self, name="genesismesh"):
        path = self.root / name
        path.mkdir()
        self.git(path, "init")
        self.git(path, "config", "user.name", "Test Developer")
        self.git(path, "config", "user.email", "test@example.invalid")
        self.git(path, "remote", "add", "origin", f"https://github.com/GenesisMeshLabs/{name}.git")
        return path

    def call(self, main, args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--workspace", str(self.root), *args])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_manifest_selections_and_unknown_names(self):
        args = argparse.Namespace(all=False, repo=None, profile=None)
        self.assertEqual([r["name"] for r in workspace.selected(self.manifest, args)], ["genesismesh", "gateway"])
        args.repo = ["sdk-go", "sdk-go", "devtools"]
        self.assertEqual([r["name"] for r in workspace.selected(self.manifest, args)], ["sdk-go", "devtools"])
        args.repo = ["not-a-project"]
        with self.assertRaises(ValueError):
            workspace.selected(self.manifest, args)

    def test_manifest_derives_repositories_from_profiles(self):
        names = [r["name"] for r in self.manifest["repositories"]]
        members = [n for group in self.manifest["profiles"].values() for n in group]
        self.assertEqual(names, list(dict.fromkeys(members)))
        sandbox = next(r for r in self.manifest["repositories"] if r["name"] == "sandbox")
        self.assertEqual(sandbox, {"name": "sandbox", "in_all": True, "tools": {}, "install": [], "test": []})
        args = argparse.Namespace(all=True, repo=None, profile=None)
        everyday = [r["name"] for r in workspace.selected(self.manifest, args)]
        self.assertNotIn("site", everyday)
        self.assertIn("gateway", everyday)
        args = argparse.Namespace(all=False, repo=None, profile="extras")
        self.assertIn("site", [r["name"] for r in workspace.selected(self.manifest, args)])

    def test_manifest_rejects_traversal_duplicates_and_orphan_recipes(self):
        raw = json.loads((workspace.ROOT / "repos.json").read_text(encoding="utf-8"))
        cases = [
            lambda d: d["profiles"]["core"].append("../escape"),
            lambda d: d["profiles"]["core"].append("gateway"),
            lambda d: d["recipes"].update(missing={"install": [["npm", "ci"]]}),
            lambda d: d["recipes"]["sdk-go"].update(tools={"unknown-tool": None}),
            lambda d: d["recipes"]["sdk-go"].update(install="go mod download"),
            lambda d: d["recipes"]["sdk-go"].update(test=[[]]),
            lambda d: d["recipes"]["sdk-go"].update(commands=[["go", "test"]]),
            lambda d: d.update(default_profile="missing"),
            lambda d: d.update(excluded_from_all=["core"]),
            lambda d: d.update(excluded_from_all=["missing"]),
        ]
        for change in cases:
            data = json.loads(json.dumps(raw))
            change(data)
            file = self.root / "bad.json"
            file.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                workspace.load_manifest(file)

    def test_dry_run_with_every_option_does_not_write(self):
        with patch("bootstrap.run") as run:
            code, output, _ = self.call(bootstrap.main, [
                "--all", "--dry-run", "--install", "--agents",
                "--git-email", "test@example.invalid", "--github-user", "test-user",
            ])
        self.assertEqual(code, 0)
        self.assertIn("Preview complete: 11 selected, 0 failed", output)
        self.assertEqual(list(self.root.iterdir()), [])
        run.assert_not_called()

    def test_script_dry_run_does_not_create_bytecode(self):
        scripts = self.root / "scripts"
        scripts.mkdir()
        for name in ["bootstrap.py", "workspace.py", "repos.json"]:
            shutil.copy(workspace.ROOT / name, scripts / name)
        result = subprocess.run([
            sys.executable, str(scripts / "bootstrap.py"), "--repo", "sdk-go",
            "--workspace", str(self.root / "new workspace"), "--dry-run",
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((scripts / "__pycache__").exists())
        self.assertFalse((self.root / "new workspace").exists())

    def test_existing_dirty_checkout_is_preserved(self):
        repo = self.checkout()
        (repo / "work.txt").write_text("uncommitted work", encoding="utf-8")
        before = (repo / ".git" / "config").read_bytes()
        with patch("bootstrap.run") as run:
            code, _, _ = self.call(bootstrap.main, ["--repo", "genesismesh"])
        self.assertEqual(code, 0)
        run.assert_not_called()
        self.assertEqual((repo / "work.txt").read_text(), "uncommitted work")
        self.assertEqual((repo / ".git" / "config").read_bytes(), before)

    def test_identity_changes_are_local_and_explicit(self):
        repo = self.checkout()
        code, _, _ = self.call(bootstrap.main, [
            "--repo", "genesismesh", "--git-name", "Personal Developer",
            "--git-email", "personal@example.invalid", "--github-user", "personal-user",
        ])
        self.assertEqual(code, 0)
        self.assertEqual(self.git(repo, "config", "--local", "user.email"), "personal@example.invalid")
        self.assertEqual(self.git(repo, "config", "--local", "credential.https://github.com.username"), "personal-user")

    def test_dry_run_preserves_existing_identity_and_guidance(self):
        repo = self.checkout()
        (repo / "AGENT.md").write_text("project instructions", encoding="utf-8")
        before = (repo / ".git" / "config").read_bytes()
        code, _, _ = self.call(bootstrap.main, ["--repo", "genesismesh", "--dry-run", "--agents", "--git-email", "new@example.invalid"])
        self.assertEqual(code, 0)
        self.assertEqual((repo / ".git" / "config").read_bytes(), before)
        self.assertEqual((repo / "AGENT.md").read_text(), "project instructions")

    def test_wrong_origin_is_rejected_without_changes(self):
        repo = self.checkout()
        self.git(repo, "remote", "set-url", "origin", "https://github.com/another-owner/genesismesh.git")
        before = (repo / ".git" / "config").read_bytes()
        code, _, error = self.call(bootstrap.main, ["--repo", "genesismesh", "--git-email", "new@example.invalid"])
        self.assertEqual(code, 1)
        self.assertIn("Origin does not match", error)
        self.assertEqual((repo / ".git" / "config").read_bytes(), before)

    def test_non_repository_is_rejected(self):
        (self.root / "genesismesh").mkdir()
        code, _, _ = self.call(bootstrap.main, ["--repo", "genesismesh"])
        self.assertEqual(code, 1)

    def test_clone_uses_argument_list_and_optional_username(self):
        seed = self.checkout("seed")
        (seed / "README.md").write_text("local clone fixture", encoding="utf-8")
        self.git(seed, "add", "README.md")
        self.git(seed, "commit", "-m", "fixture")

        def local_clone(command):
            # Exercise a real clone without network access. Restore the intended
            # origin afterward so repeat-run validation uses the real URL.
            result = workspace.run(["git", "clone", "--", str(seed), command[-1]], capture=True)
            self.git(Path(command[-1]), "remote", "set-url", "origin", command[-2])
            return result

        with patch("bootstrap.run", side_effect=local_clone) as run:
            code, _, _ = self.call(bootstrap.main, ["--repo", "sdk-go", "--github-user", "personal-user"])
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args_list[0].args[0], [
            "git", "clone", "--", "https://personal-user@github.com/GenesisMeshLabs/sdk-go.git", str(self.root / "sdk-go")
        ])
        self.assertEqual((self.root / "sdk-go" / "README.md").read_text(), "local clone fixture")
        self.assertEqual(self.git(self.root / "sdk-go", "config", "--local", "credential.https://github.com.username"), "personal-user")
        code, output, _ = self.call(bootstrap.main, ["--repo", "sdk-go"])
        self.assertEqual(code, 0)
        self.assertIn("Existing checkout preserved", output)

    def test_failure_does_not_prevent_other_repositories(self):
        (self.root / "genesismesh").mkdir()
        self.checkout("sdk-go")
        code, output, _ = self.call(bootstrap.main, ["--repo", "genesismesh", "--repo", "sdk-go"])
        self.assertEqual(code, 1)
        self.assertIn("Existing checkout preserved", output)
        self.assertIn("2 selected, 1 failed", output)

    def test_guidance_updates_preserve_project_text_and_are_idempotent(self):
        repo = self.checkout()
        file = repo / "AGENT.md"
        file.write_text("Project-specific rules\n", encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            workspace.install_guidance(repo, False)
            first = file.read_bytes()
            workspace.install_guidance(repo, False)
        self.assertEqual(first, file.read_bytes())
        self.assertTrue(file.read_text().startswith("Project-specific rules\n"))
        self.assertIn("GenesisMeshLabs shared guidance", file.read_text())
        self.assertNotIn("Devtools development", file.read_text())
        file.write_text("prefix\n" + workspace.BEGIN + "\nold shared rules\n" + workspace.END + "\nsuffix\n", encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            workspace.install_guidance(repo, False)
        self.assertTrue(file.read_text().endswith("\nsuffix\n"))
        self.assertNotIn("old shared rules", file.read_text())

    def test_malformed_guidance_is_not_overwritten(self):
        repo = self.checkout()
        file = repo / "AGENT.md"
        file.write_text(workspace.BEGIN + "\nunfinished", encoding="utf-8")
        before = file.read_bytes()
        with self.assertRaises(ValueError):
            workspace.install_guidance(repo, False)
        self.assertEqual(file.read_bytes(), before)

    def test_remote_url_matching(self):
        for url in ["https://github.com/GenesisMeshLabs/sdk-go.git", "https://me@github.com/GenesisMeshLabs/sdk-go.git", "git@github.com:GenesisMeshLabs/sdk-go.git", "ssh://git@github.com/GenesisMeshLabs/sdk-go.git"]:
            self.assertTrue(workspace.remote_matches(url, "sdk-go"), url)
        for url in ["https://github.com.evil/GenesisMeshLabs/sdk-go.git", "https://github.com/other/sdk-go.git", "https://github.com/GenesisMeshLabs/sdk-go-extra.git"]:
            self.assertFalse(workspace.remote_matches(url, "sdk-go"), url)

    def test_tool_version_minimum(self):
        with patch("workspace.run", return_value=subprocess.CompletedProcess([], 0, "v22.1.0\n")):
            self.assertEqual(workspace.check_tool("node", "22"), "v22.1.0")
            with self.assertRaises(ValueError):
                workspace.check_tool("node", "23")
        self.assertEqual(workspace.version_tuple("go version go1.22.4 darwin/arm64"), (1, 22, 4))

    def test_setup_targets_selected_virtual_environment(self):
        repo = self.manifest["repositories"][0]
        commands = workspace.setup_commands(repo, self.root / "space in path")
        self.assertEqual(commands[0], [sys.executable, "-m", "venv", ".venv"])
        self.assertEqual(commands[1][0], str(workspace.venv_python(self.root / "space in path")))
        self.assertEqual(commands[1][1:4], ["-m", "pip", "install"])

    def test_existing_virtualenv_is_reused(self):
        repo = self.manifest["repositories"][0]
        python = workspace.venv_python(self.root)
        python.parent.mkdir(parents=True)
        python.touch()
        with patch("workspace.check_tool"), patch("workspace.run", return_value=subprocess.CompletedProcess([], 0, "Python 3.12.0")) as run, redirect_stdout(io.StringIO()):
            workspace.install_dependencies(repo, self.root, False)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].args[0], [str(python), "--version"])
        self.assertEqual(run.call_args_list[1].args[0][0], str(python))

    def test_broken_virtualenv_fails_without_replacing_it(self):
        (self.root / ".venv").mkdir()
        with patch("workspace.check_tool"), patch("workspace.run") as run:
            with self.assertRaises(ValueError):
                workspace.install_dependencies(self.manifest["repositories"][0], self.root, False)
        run.assert_not_called()
        self.assertTrue((self.root / ".venv").is_dir())

    def test_doctor_json_reports_missing_checkout(self):
        code, output, _ = self.call(doctor.main, ["--repo", "devtools", "--json"])
        data = json.loads(output)
        self.assertEqual(code, 1)
        self.assertFalse(data["ok"])
        self.assertTrue(any(c["check"] == "devtools: checkout" and not c["ok"] for c in data["checks"]))

    def test_doctor_reports_dirty_checkout_without_mutating_it(self):
        repo = self.checkout("devtools")
        (repo / "work").write_text("draft", encoding="utf-8")
        before = (repo / ".git" / "config").read_bytes()
        code, output, _ = self.call(doctor.main, ["--repo", "devtools", "--json"])
        self.assertEqual(code, 0)
        self.assertIn("uncommitted changes", output)
        self.assertEqual((repo / ".git" / "config").read_bytes(), before)


    def upstream_and_clone(self):
        upstream = self.checkout("upstream")
        (upstream / "README.md").write_text("v1", encoding="utf-8")
        self.git(upstream, "add", "README.md")
        self.git(upstream, "commit", "-m", "v1")
        clone = self.root / "clone"
        workspace.run(["git", "clone", "--quiet", "--", str(upstream), str(clone)], capture=True)
        self.git(clone, "config", "user.name", "Test Developer")
        self.git(clone, "config", "user.email", "test@example.invalid")
        (upstream / "README.md").write_text("v2", encoding="utf-8")
        self.git(upstream, "commit", "-am", "v2")
        return upstream, clone

    def test_pull_fast_forwards_clean_checkout(self):
        _, clone = self.upstream_and_clone()
        with redirect_stdout(io.StringIO()):
            workspace.pull_checkout(clone, True)
            self.assertEqual((clone / "README.md").read_text(), "v1")
            workspace.pull_checkout(clone, False)
        self.assertEqual((clone / "README.md").read_text(), "v2")

    def test_pull_skips_dirty_detached_and_reports_diverged(self):
        upstream, clone = self.upstream_and_clone()
        (clone / "README.md").write_text("local work", encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            workspace.pull_checkout(clone, False)
        self.assertIn("uncommitted changes", output.getvalue())
        self.assertEqual((clone / "README.md").read_text(), "local work")
        self.git(clone, "commit", "-qam", "local")
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, "diverged"):
            workspace.pull_checkout(clone, False)
        self.git(clone, "checkout", "-q", "--detach")
        output = io.StringIO()
        with redirect_stdout(output):
            workspace.pull_checkout(clone, False)
        self.assertIn("no upstream", output.getvalue())

    def test_vscode_workspace_merges_without_losing_settings(self):
        target = self.root / f"{self.root.name}.code-workspace"
        with redirect_stdout(io.StringIO()):
            workspace.update_vscode_workspace(self.root, ["genesismesh"], True)
            self.assertFalse(target.exists())
            workspace.update_vscode_workspace(self.root, ["genesismesh"], False)
        self.assertEqual(json.loads(target.read_text()), {"folders": [{"path": "genesismesh"}]})
        target.write_text(json.dumps({"folders": [{"path": "notes"}], "settings": {"a": 1}}), encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            workspace.update_vscode_workspace(self.root, ["genesismesh", "sdk-go"], False)
        data = json.loads(target.read_text())
        self.assertEqual([f["path"] for f in data["folders"]], ["notes", "genesismesh", "sdk-go"])
        self.assertEqual(data["settings"], {"a": 1})
        target.write_text("// comment\n{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            workspace.update_vscode_workspace(self.root, ["genesismesh"], False)
        self.assertEqual(target.read_text(), "// comment\n{}")

    def test_bootstrap_vscode_lists_only_cloned_projects(self):
        self.checkout("sdk-go")
        (self.root / "gateway").mkdir()
        code, _, _ = self.call(bootstrap.main, ["--repo", "sdk-go", "--vscode"])
        self.assertEqual(code, 0)
        data = json.loads((self.root / f"{self.root.name}.code-workspace").read_text())
        self.assertEqual(data["folders"], [{"path": "sdk-go"}])

    def test_doctor_org_reports_drift(self):
        live = json.dumps([{"name": r["name"]} for r in self.manifest["repositories"]])
        with patch("doctor.run", return_value=subprocess.CompletedProcess([], 0, live)):
            self.assertIn(f"all {len(self.manifest['repositories'])}", doctor.organization_drift(self.manifest))
        drifted = json.dumps([{"name": "new-repo"}, *json.loads(live)[1:]])
        with patch("doctor.run", return_value=subprocess.CompletedProcess([], 0, drifted)):
            with self.assertRaisesRegex(ValueError, "missing from repos.json: new-repo.*not found on GitHub.*genesismesh"):
                doctor.organization_drift(self.manifest)

    def test_runtests_reports_each_project(self):
        self.checkout("sdk-go")
        with patch("runtests.check_tool"), patch("runtests.run") as run:
            code, output, _ = self.call(runtests.main, ["--repo", "sdk-go", "--repo", "gateway", "--repo", "sandbox"])
        self.assertEqual(code, 0)
        run.assert_called_once_with(["go", "test", "./..."], self.root / "sdk-go")
        self.assertRegex(output, r"passed +sdk-go")
        self.assertRegex(output, r"not cloned +gateway")
        self.assertRegex(output, r"skipped +sandbox")
        with patch("runtests.check_tool"), patch("runtests.run", side_effect=ValueError("go failed (exit 1)")):
            code, output, _ = self.call(runtests.main, ["--repo", "sdk-go"])
        self.assertEqual(code, 1)
        self.assertRegex(output, r"failed +sdk-go")


if __name__ == "__main__":
    unittest.main()
