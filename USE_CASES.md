# Agentic Harness Use Cases

Agentic Harness is useful when an AI-assisted task needs a repeatable lifecycle: start a goal, run a worker, preserve artifacts, review the output, and stop when the review contract is satisfied.

## 1. CI Reviewer

What it does: runs an automated review pass on a pull request or local branch, collects command output, and records whether the branch meets explicit criteria such as tests passing, lint passing, and required files changing.

How the harness enables it: the GitHub Actions or shell adapter can run the review commands, the artifact store records reports, and deterministic review gates decide whether the goal is complete instead of relying on a model summary.

Target audience: maintainers, solo developers, small engineering teams, and consultants who want consistent pre-merge checks without building a custom control loop.

## 2. Documentation Generator

What it does: drafts or refreshes docs from a bounded source such as recent commits, a module, a CLI help surface, or a release checklist.

How the harness enables it: a shell, tmux, or local LLM worker can generate the draft, then review criteria can require the expected Markdown file to exist and optional commands to pass before the run is treated as complete.

Target audience: open-source maintainers, developer tooling teams, API owners, and technical consultants.

## 3. Release Notes Assistant

What it does: turns commits, changelog fragments, and test results into a release note draft with links to artifacts proving the package was tested.

How the harness enables it: project-local state keeps each release run separate, adapters can run build and test commands, and review gates can check for a generated release note plus a clean test command.

Target audience: Python package maintainers, internal platform teams, and small product teams shipping frequent releases.

## 4. Data Cleanup Runner

What it does: executes a bounded cleanup task, such as normalizing CSV rows, validating JSON files, or preparing migration reports, while keeping the generated diff and logs inspectable.

How the harness enables it: the shell adapter can call existing cleanup scripts, artifacts can include before/after reports, and review criteria can require validation commands to pass.

Target audience: operations teams, data analysts, automation consultants, and teams that need auditable batch work.

## 5. Local LLM Task Harness

What it does: runs local or OpenAI-compatible LLM tasks in a controlled project directory, such as drafting summaries, classifying tickets, or proposing code changes for human review.

How the harness enables it: the local LLM adapter provides a consistent worker interface, project-local state keeps runs isolated, and the loop guard prevents unattended continuation from repeating indefinitely.

Target audience: AI infrastructure operators, privacy-sensitive teams, researchers, and developers testing local model workflows.

## Getting Started: CI Reviewer

1. Install the package:

   ```bash
   pipx install git+https://github.com/moortekweb-art/agentic-harness.git
   ```

2. Initialize a project-local harness:

   ```bash
   agentic-harness init
   ```

3. Configure a shell worker that runs the same checks you expect before review:

   ```yaml
   version: 1
   worker:
     type: shell
     shell_command:
       - bash
       - -lc
       - "python -m pytest tests/ -q && python -m ruff check ."
   review:
     criteria:
       - type: command_passes
         command: "python -m pytest tests/ -q"
       - type: command_passes
         command: "python -m ruff check ."
   ```

4. Run the review goal:

   ```bash
   agentic-harness run "review this branch before merge"
   agentic-harness status
   ```

## Getting Started: Documentation Generator

1. Initialize the harness in the repository that needs docs:

   ```bash
   agentic-harness init
   ```

2. Configure a worker script that writes the target document:

   ```yaml
   version: 1
   worker:
     type: shell
     shell_command:
       - bash
       - -lc
       - "python scripts/generate_docs.py > docs/generated.md"
   review:
     criteria:
       - type: artifact_exists
         path: docs/generated.md
       - type: command_passes
         command: "test -s docs/generated.md"
   ```

3. Start with a bounded objective:

   ```bash
   agentic-harness start "generate docs for the CLI commands changed this week"
   agentic-harness continue
   agentic-harness review
   ```

4. Inspect `.agentic-harness/` for the recorded goal state, review result, and artifacts before committing the generated documentation.
