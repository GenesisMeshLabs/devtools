# Contributing

Keep setup small, predictable, and cross-platform. Project dependencies belong
in that project's own manifest, not a global requirements file here.

When adding a repository, add it to a profile in repos.json and to the profile table in
README.md and ARCHITECTURE.md. `doctor.py --org` lists repositories that are missing.
Add a `recipes` entry only when you have verified its toolchain minimums and its install
or test commands upstream (test commands should mirror the project's CI). Repositories
without one are clone-only, and their own README remains the setup guide.

Recipes execute argument lists without a shell. `{python}` expands to the running
interpreter and `{venv_python}` to the selected checkout's virtual-environment Python.
Do not place credentials, usernames, or machine-specific paths in this repository.

For behavioral changes, add regression tests using temporary directories. Tests must
not require GitHub authentication, external services, package installation, or real user
Git configuration. Cover dry runs, existing checkouts, failure handling, and Windows paths.

Before submitting:

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q .
git diff --check
```

Describe the change, its effect on existing workspaces, and the checks you ran.
