# Agent guidance

<!-- shared-guidance:begin -->
## GenesisMeshLabs shared guidance

These are common defaults for GenesisMeshLabs projects. Follow the user's task
and the repository's more specific instructions when they differ from these defaults.

- Read the repository README, AGENT.md, and applicable nested instructions before editing.
- Keep protocol, transport, application, CLI, and presentation responsibilities separate.
- Treat identity, signature verification, revocation, and policy checks as trust boundaries.
  Preserve fail-closed behavior and backward compatibility of signed payloads.
- Keep changes focused on the requested task and preserve unrelated working-tree changes.
- Use the project's own dependency manifests, lockfiles, and validation commands.
  Keep Python dependencies in the repository's virtual environment.
- Add meaningful regression tests for behavioral fixes, especially negative paths.
- Run relevant checks and report actual results, including failures or checks not run.
- Never place credentials, private keys, access tokens, or real environment files in source control.
- Verify the Git commit identity and push account before publishing. They are separate settings.
- Follow the repository's release process. Do not bypass hooks or rewrite shared history.
- Explain what changed, why, and how it was verified. Use plain language.
- Never use em dashes in responses, documentation, or content you create or edit.
  Use commas, colons, parentheses, periods, or ordinary hyphens instead.

This file contains instructions only. It does not install, authenticate, or grant
permissions to an agent. Configure your chosen agent to read AGENT.md.
<!-- shared-guidance:end -->

## Devtools development

Read README.md before changing this repository. Only the marked shared section above
is distributed by `bootstrap.py --agents`.

This repository owns local developer setup for GenesisMeshLabs. Product code,
runtime dependencies, and project release policies belong in their own repositories.

- Keep the tools standard-library-only and compatible with Python 3.12 or later.
- Support macOS, Linux, and Windows using pathlib and argument-list subprocess calls.
- Preserve existing checkouts. Never reset, stash, switch branches, or overwrite files.
  Pulling happens only with `--pull`, and only as a fast-forward of a clean checkout.
- Default setup clones repositories only. Pulling, dependency installation, Git identity
  settings, VS Code workspace files, and AGENT.md updates require explicit options.
- A dry run must not write files, clone repositories, install packages, or change Git configuration.
- Never print credential-helper output, access tokens, or environment secrets.
- Keep repo selection (profiles) and install/test recipes in repos.json. Check upstream
  manifests and CI before changing a minimum version or command.
- Keep agent guidance vendor-neutral. Use AGENT.md only; no dedicated agent configuration.
- Test with `python -m unittest discover -s tests -v` and run `python -m compileall -q .`.
- Never use em dashes in content you create or edit.
