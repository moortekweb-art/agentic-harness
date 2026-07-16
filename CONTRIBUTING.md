# Contributing to Agentic Harness

Thanks for helping improve Agentic Harness. Contributions should keep the
public package portable, local-first, provider-neutral, and honest about which
security boundaries the shared engine can enforce.

## Before starting

- Search existing issues and pull requests for related work.
- Open an issue before a broad API, state-schema, dependency, or product-boundary
  change so the scope can be agreed first.
- Report security-sensitive findings through [SECURITY.md](SECURITY.md), not a
  public issue containing exploit details.
- Keep one pull request focused on one coherent outcome.

Machine-specific services, private model routes, credentials, and local
operator topology belong in deployment configuration or optional adapters, not
in default product behavior or public examples.

## Development setup

Agentic Harness supports Python 3.11 through 3.14. From a fresh clone:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[test]"
python -m pytest tests/ -q
```

On Windows PowerShell, activate the environment with
`.venv\Scripts\Activate.ps1`.

## Make a focused change

1. Create a branch from the current `main` branch.
2. Preserve unrelated and pre-existing worktree changes.
3. Add or update tests before changing behavior, then confirm the new test
   fails for the intended reason.
4. Implement the smallest change that satisfies the test.
5. Update the canonical documentation when commands, configuration, public
   APIs, security boundaries, or operator behavior change.
6. Run the relevant checks and inspect the final diff for secrets, generated
   files, and accidental scope expansion.

Match the existing Python style: four-space indentation, type annotations for
public and non-trivial code, descriptive `snake_case` names, and concise
comments that explain why rather than restating the code.

## Verification

Run the full local stack for code changes:

```bash
python -m pytest tests/ -q
python -m ruff check
python -m mypy agentic_harness
python -m compileall agentic_harness
```

For browser JavaScript changes, also run:

```bash
node --check agentic_harness/gui/static/app.js
node tests/frontend_token_race_test.js
```

Then launch a disposable project with
`agentic-harness-gui --project-dir /path/to/disposable-project --no-open` and
verify Home, Tasks, History, and Settings by pointer and keyboard at desktop and
narrow widths. Confirm there is no horizontal overflow and that managed
Settings is visible but read-only.

For packaging, entry-point, packaged-asset, or release-pipeline changes, run:

```bash
python -m agentic_harness.cli release-smoke --dist-dir dist
```

Start with focused tests while iterating, but run the complete applicable stack
before requesting review. If a check cannot run in your environment, name the
missing check and the reason in the pull request.

## Test and documentation expectations

- Test observable behavior, failure paths, and boundary conditions rather than
  implementation details.
- Keep fixtures synthetic and free of credentials, private paths, hostnames,
  model names, and operator-only infrastructure.
- Maintain compatibility across Linux, macOS, Windows, and supported Python
  versions unless a documented compatibility decision says otherwise.
- Update existing canonical docs instead of adding overlapping `final`,
  `latest`, or version-suffixed guides.
- Clearly distinguish a deterministic mock/demo from evidence about real model
  quality or agent performance.

## Pull requests

A pull request should include:

- a concise problem statement and summary of the solution;
- the tests and commands run, with their results;
- any compatibility, migration, security, or release impact;
- a linked issue when one exists; and
- screenshots for user-visible GUI changes.

Use a short imperative commit subject, preferably Conventional Commit style,
such as `fix: preserve review evidence on restart`. Do not commit virtual
environments, caches, build output, run state, credentials, or private reports.

By contributing, you agree that your contribution is licensed under the
project's [MIT License](LICENSE).
