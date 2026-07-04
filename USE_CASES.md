# Agentic Harness Use Cases

Agentic Harness is best for bounded AI or automation work where the operator needs project-local state, inspectable artifacts, and deterministic review before declaring the work done.

## 1. Documentation and Release Note Automation

**Description:** Generate changelogs, release notes, migration notes, or README updates from recent commits, issue summaries, and existing docs.

**How the harness enables it:** A shell or local LLM worker can draft the document, save it as an artifact, and run review gates such as `file_changed`, `command_passes`, and `git_clean`. The loop guard keeps rewrite cycles bounded.

**Target audience:** Maintainers of small open source projects, internal platform teams, and solo developers who want repeatable documentation updates without handing full repo control to an agent.

## 2. CI-Backed Code Maintenance Tasks

**Description:** Run narrowly scoped code maintenance goals such as formatting cleanup, dependency compatibility checks, small test fixes, or generated file refreshes.

**How the harness enables it:** The GitHub Actions adapter can dispatch work to CI and wait on the exact workflow run, while deterministic review verifies tests, lint, type checks, or generated artifacts before the task is marked complete.

**Target audience:** Engineering teams that want agent-assisted maintenance to happen inside existing CI controls instead of on an unreviewed workstation.

## 3. Local Knowledge Base Curation

**Description:** Summarize notes, normalize markdown files, extract action items, or keep a project-local knowledge base current.

**How the harness enables it:** A shell worker can call existing scripts against local files, record the changed documents as artifacts, and use review commands to validate links, front matter, or formatting.

**Target audience:** Consultants, researchers, technical writers, and operations teams with private local notes that should not be uploaded to hosted agent platforms.

## 4. Long-Running Operational Checklists

**Description:** Execute multi-step operational runbooks such as pre-release checks, deployment readiness audits, migration rehearsals, or service health reviews.

**How the harness enables it:** A tmux worker can run an inspectable long-running command while the harness preserves goal state, reports, and review outcomes in `.agentic-harness/`. Operators can attach to the session when manual inspection is needed.

**Target audience:** DevOps engineers, SREs, and technical founders who need automation support while preserving human review points.

## 5. Local LLM Evaluation and Prompt Iteration

**Description:** Run prompt experiments or local-model smoke tests and capture the request, response, and pass/fail criteria for each goal.

**How the harness enables it:** The local LLM adapter calls an OpenAI-compatible endpoint only when configured, stores run state locally, and can pair model output with deterministic review commands such as schema validation or compile checks.

**Target audience:** AI engineers, privacy-sensitive teams, and developers evaluating self-hosted LLMs before wiring them into production workflows.

## Getting Started

### Documentation and Release Notes

1. Install the CLI:

```bash
pipx install git+https://github.com/moortekweb-art/agentic-harness.git
```

2. Initialize the project and configure a shell worker:

```bash
agentic-harness init
cat > .agentic-harness/config.yml <<'YAML'
version: 1
worker:
  type: shell
  shell_command:
    - python
    - scripts/write_release_notes.py
review_command:
  - python
  - -m
  - pytest
  - tests/
  - -q
YAML
```

3. Run a bounded documentation goal:

```bash
agentic-harness run "draft release notes for the last three commits"
```

### CI-Backed Code Maintenance

1. Create a workflow that accepts `goal_id` and `objective` inputs.

2. Configure the GitHub Actions worker with a token from your secret store:

```yaml
version: 1
worker: github_actions
github_owner: your-org
github_repo: your-repo
github_workflow_id: maintenance.yml
github_token: token-from-your-secret-store
github_wait: true
github_timeout: 600
review_command:
  - python
  - -m
  - pytest
  - tests/
  - -q
```

3. Start with a small maintenance objective:

```bash
agentic-harness start "refresh generated docs and verify tests"
agentic-harness continue
agentic-harness review
```
