# GenesisMeshLabs devtools

Workspace setup for [GenesisMeshLabs](https://github.com/GenesisMeshLabs): clone every
project, keep them up to date, install dependencies, run tests, and check toolchains.
Each project keeps ownership of its own dependencies, lockfiles, tests, and releases.

New here? Read [ARCHITECTURE.md](ARCHITECTURE.md) for how the projects fit together.

## Quick start

Requires **Python 3.12+ and Git**; the scripts use only the standard library.
On Windows, replace `python3` with `py -3.12`.

```sh
mkdir GM && cd GM
git clone https://github.com/GenesisMeshLabs/devtools.git
python3 devtools/bootstrap.py --all --vscode
python3 devtools/doctor.py --all
```

This clones every project next to `devtools` and creates `GM.code-workspace`, which
opens all of them in one VS Code window. Doctor then lists missing toolchains.
Add `--dry-run` to any bootstrap command to preview it.

## Daily use

```sh
python3 devtools/bootstrap.py --all --pull     # clone new projects, fast-forward existing ones
python3 devtools/runtests.py --all             # run every project's tests
python3 devtools/doctor.py --all --org         # check toolchains and that repos.json matches GitHub
```

`--pull` only fast-forwards the current branch from its upstream. Checkouts with
uncommitted changes, without an upstream, or with diverged history are left untouched
and reported. Bootstrap never resets, stashes, switches branches, commits, or pushes.

`runtests.py` runs the main test command from each project's CI. It skips projects that
have no test command or are not cloned, and prints a pass/fail summary. Linters, audits,
and integration tests stay in each project's own CI.

## Select projects

Every script accepts the same selectors. The default is the `core` profile.

```sh
python3 devtools/bootstrap.py --profile sdks
python3 devtools/runtests.py --repo genesismesh --repo sdk-go
python3 devtools/doctor.py --all --json
```

| Profile | Projects |
| --- | --- |
| `core` | [genesismesh](https://github.com/GenesisMeshLabs/genesismesh), [gateway](https://github.com/GenesisMeshLabs/gateway) |
| `sdks` | [sdk-typescript](https://github.com/GenesisMeshLabs/sdk-typescript), [sdk-go](https://github.com/GenesisMeshLabs/sdk-go), [sdk-dotnet](https://github.com/GenesisMeshLabs/sdk-dotnet), [sdk-rust](https://github.com/GenesisMeshLabs/sdk-rust) |
| `community` | [.github](https://github.com/GenesisMeshLabs/.github), [devtools](https://github.com/GenesisMeshLabs/devtools), [connectorzzz-dev](https://github.com/GenesisMeshLabs/connectorzzz-dev), [genesismesh-content](https://github.com/GenesisMeshLabs/genesismesh-content), [sandbox](https://github.com/GenesisMeshLabs/sandbox) (private) |
| `extras` | [site](https://github.com/GenesisMeshLabs/site), [genesismesh-web](https://github.com/GenesisMeshLabs/genesismesh-web), [genesis-quantum-lab](https://github.com/GenesisMeshLabs/genesis-quantum-lab), [genesis-world-lab](https://github.com/GenesisMeshLabs/genesis-world-lab) |

`--all` selects every profile except `extras`; use `--profile extras` to clone those too.
Run `doctor.py --org` to find repositories created on GitHub but not yet added to
[repos.json](repos.json).

## Install dependencies

Install the language runtimes first (`doctor.py` lists what is missing), then:

```sh
python3 devtools/bootstrap.py --all --install
```

The install and test commands live under `recipes` in [repos.json](repos.json). Projects
without a recipe are clone-only; follow their README. Python projects get a local `.venv`;
nothing is installed into the system Python. Existing environments are reused, and broken
ones are reported rather than deleted. .NET tests need the .NET 8 runtime even with a newer SDK.

## Commit identity

Commit name/email and the GitHub account used for pushing are separate settings. To set
them for the selected repositories only:

```sh
python3 devtools/bootstrap.py --all --git-name "Your Name" --git-email you@example.com --github-user your-github-user
```

`--github-user` picks the HTTPS credential username; it does not log you in. Global Git
settings and existing commits are never changed.

## Agent guidance

[AGENT.md](AGENT.md) holds shared, vendor-neutral guidance for coding agents.
`bootstrap.py --agents` copies it into a marked section of each project's `AGENT.md`,
preserving everything else in that file. Configure your agent to read `AGENT.md`.

## Develop these tools

```sh
cd devtools
python3 -m unittest discover -s tests -v
python3 bootstrap.py --all --dry-run
```

All scripts exit `0` on success, `1` if anything failed, and `2` for invalid arguments.
A failure in one project does not stop the others. See [CONTRIBUTING.md](CONTRIBUTING.md).
