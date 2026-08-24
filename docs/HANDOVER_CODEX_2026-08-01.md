# Handover to Codex — Local Studio incident and harness verification

**Prepared:** 2026-08-01
**Repo:** `moortekweb-art/agentic-harness`
**Prepared on branch:** `claude/charming-hawking-y825i0`

**Access scope of the session that produced this document:** the GitHub repository only. No access to the controller Mac, the Node1 inference host, or the private tailnet. Every statement about those machines is an inference from repository evidence and is labelled as such. Everything labelled VERIFIED was executed and observed directly.

> Internal hostnames and personal filesystem paths are deliberately generic in this document (`<node1-host>`, `<controller-home>`) because this file lives in a distributed repository. Substitute the real values locally.

---

## 0. TL;DR

| # | Item | Status | Where the fix lives |
|---|------|--------|---------------------|
| 1 | Unpushed work on the controller Mac (permission fix + 9 tests) | **AT RISK** | Controller Mac local clone |
| 2 | Node1 deploy split — package 0.13.5 vs tests 0.13.6 | Diagnosed, unfixed | Node1 install |
| 3 | `local-node1-goal-integration-audit.json` unreadable | Diagnosed, unfixed | Controller Mac filesystem |
| 4 | "Harness closed a goal fully automatically" | **UNVERIFIED** | See §4 |

The repository itself is healthy and needs no repair. Both reported "repair targets" are environment problems, not code defects.

---

## 1. VERIFIED — repository health

Full suite, executed directly:

```
python -m pytest tests/ -q
1491 passed, 2 skipped, 12 warnings in 96.44s
```

Targeted run on the two files implicated in the incident:

```
python -m pytest tests/test_managed_route_contract.py tests/test_gui_api.py -q
208 passed in 28.24s
```

**Commit accuracy note.** These ran against `85578f9`. `origin/main` is `a877379`, two commits ahead. That diff is `docs/assets/agentic-harness-gui.png` and `docs/assets/agentic-harness-gui-mobile.png` — **two binary screenshots, zero `.py` files** — so the result carries to `origin/main` unchanged. Re-run if you want the claim stated against `a877379` exactly.

The single skipped test and the 12 warnings are pre-existing (`LocalLLMAdapter` deprecation in `tests/test_adapters.py`) and unrelated.

---

## 2. VERIFIED — repair target 1 is a deploy defect, not a code defect

**Reported symptom:** `tests/test_managed_route_contract.py` cannot import `_managed_workspace_identity` from `agentic_harness.gui.server`, read as a deployed source/test contract mismatch.

**Finding: the contract is intact on both sides in this repo.**

- Definition: `agentic_harness/gui/server.py:1533` — `_managed_workspace_identity(bridge, *, project_dir=None) -> dict[str, str]`
- Production use: `agentic_harness/gui/server.py:172-187`, feeding both `GuiSession(workspace_identity=...)` and the session-path hash
- Test import: `tests/test_managed_route_contract.py:23-28`
- Test use: `tests/test_managed_route_contract.py:1858`, `1881-1882` — signature matches exactly (positional bridge, keyword `project_dir`)

**The decisive fact.** The function and the test that imports it landed in **the same commit**:

```
a1d04136832ab6d754b61514f96aef8d80610e09
"fix managed GUI and verifier boundaries"
2026-07-28T21:43:57-07:00
ancestor of main, 17 commits back
also bumped pyproject.toml: 0.13.5 -> 0.13.6
```

Because source and test are atomic in one commit, **no clean checkout at any commit can produce that ImportError.** Reproducing it requires tests at ≥ `a1d0413` resolving against package code at < `a1d0413` — two different versions loaded at once.

Corroborating detail: the reported error is *"cannot import name"*, not *"No module named"*. The package imported successfully; it was simply an older copy.

**Diagnosis: partial deploy.** Most likely a stale non-editable install in `site-packages` shadowing a newer git checkout. `Version == 0.13.5` while the checkout sits at or after `a1d0413` is the signature.

---

## 3. VERIFIED — repair target 2 is outside this repository

`local-node1-goal-integration-audit.json` has **zero references anywhere in the repo** — grepped across `.py`, `.md`, and `.yml`. It is produced by Node1 `local-goal` tooling in the documentation tree. No change in this repository can fix it.

**Inferred cause (not directly verified — no filesystem access):** ownership mismatch. The harness writes state files `0o600` and state directories `0o700` throughout:

- `agentic_harness/core/events.py:58`
- `agentic_harness/core/tournament.py:277`
- `agentic_harness/core/config.py:396`
- `agentic_harness/gui/server.py:1615`

A `0o600` file written by one account and read by another produces exactly the reported permission denied. Check the numeric owner, not only the mode, and check the parent directory too — a `0o700` directory owned by the wrong user denies reads regardless of file mode.

**Do not loosen the modes.** The restrictive permissions are a deliberate security contract. Fix ownership instead.

---

## 4. UNVERIFIED — the "fully self-closing run" claim

A Node1 sub-agent thread reported that run `20260731T200851Z` completed a goal end to end with no human input: the worker entered the harness repo, added a "First time using this?" GUI guide, wrote 9 regression tests, an independent checker ran them (9 passed), a review ran 40+ checks (all passed), and the harness accepted its own work and freed the lane in roughly two minutes.

**This could not be confirmed or refuted.** Treat it as an open question, not a settled result.

### 4a. The work never reached GitHub

Verified three independent ways:

1. `grep -rn "First time using this" .` over the working tree — no match
2. `git log --all -S "First time using this"` across all refs and all 59 branches — no match
3. GitHub PR listing — newest is **#77, last updated 2026-07-31T17:24:15Z**. Run `20260731T200851Z` began at **20:08 UTC**, nearly three hours later, and produced no branch, no commit, and no PR.

The GUI guide, the relabelled objective box, the 9 tests, and both halves of the permission fix exist **only in the local clone on the controller Mac**.

### 4b. The claim is structurally circular

The checker that ran the tests, the review that ran 40+ checks, and the acceptance that closed the lane are all components of the harness. The evidence that the harness works is the harness reporting on itself.

This inverts the project's own thesis — "only mark work done when independent checks pass." The 9 tests do not break the circle either: the worker wrote them to pin its own change, so re-running them demonstrates self-consistency and determinism, not correctness.

### 4c. A specific unresolved concern

That run executed in the same environment diagnosed in §2 as carrying a 0.13.5/0.13.6 split. **Nothing in the thread indicates the split was fixed before the run.** If it was still present, the "40+ checks passed" ran against stale package code.

There is a tell already in the thread. Sub-agent #2 reported "all tests clean **except one failure proven to predate the change**." No such failure exists in this repo — 1491 pass, zero fail. An unexplained lone failure in that environment and a known version split in that environment are plausibly the same fact.

**Falsifiable prediction: re-run that test after the §5 Phase 2 reinstall. It is expected to disappear.** If it survives a clean reinstall, it is a real defect and needs its own investigation.

### 4d. What counts in the claim's favour

Stated for fairness. The report volunteers failure in detail — a refused invalid checker, 24 consecutive permission rejections, an honest red finish — and self-reports three defects at the end. Fabricated success reports rarely do this.

The permission diagnosis is also structurally consistent with real code here: in the managed path, allowed paths are rendered into the ticket as prose (`agentic_harness/core/local_goal_bridge.py:1029,1070`) while enforcement is a separate layer. "Ticket says yes, bouncer says no" is a shape this code can genuinely produce. Wiring exists at `local_goal_bridge.py:528,539,561,575`.

**Assessment: plausible, internally coherent, probably did roughly what it claims — and not established.**

### 4e. What would settle it

1. Push the controller Mac work (§5 Phase 0), then run the 9 tests against a clean, correctly installed tree — independently of the system making the claim.
2. `pip show local-agentic-harness` on Node1. The version alone indicates whether those 40+ checks were meaningful.
3. The receipts and evidence from run directory `20260731T200851Z`.

---

## 5. Repair plan

### Phase 0 — rescue the unpushed work (do this first)

Highest-value action available. A verified permission fix that unblocks every cross-repo goal, plus 9 regression tests, currently exist on exactly one machine with no backup.

From the controller Mac clone:

```
git checkout -b agent/worker-permission-fix-20260731
git add -p          # review each hunk; do not blanket-add
git commit
git push -u origin agent/worker-permission-fix-20260731
```

Then open a draft PR. The diff can be reviewed and the tests re-run against a clean tree, which also resolves §4.

### Phase 1 — confirm the deploy split on Node1 (read-only, ~2 min)

```
python -c "import agentic_harness, agentic_harness.gui.server as s; \
print(agentic_harness.__file__); print(hasattr(s,'_managed_workspace_identity'))"
pip show local-agentic-harness | grep -E "Version|Location|Editable"
git -C <checkout> rev-parse HEAD
```

Confirmed if `__file__` points at `site-packages` rather than the checkout, or if `Version` reads 0.13.5 while the checkout sits at or after `a1d0413`.

### Phase 2 — realign the deployment

```
python -m pip uninstall -y local-agentic-harness
python -m pip install -e ".[test]"
```

This matches CI exactly (asserted at `tests/test_ci_workflow.py:102,129`). If the deploy intentionally uses a non-editable wheel, rebuild it from the current checkout instead — the requirement is that package and tests originate from the same commit. Re-run the two test files; expect 208 passed.

### Phase 3 — fix the audit file on the controller Mac

```
stat -f '%Su %Sg %Sp' <path>/local-node1-goal-integration-audit.json   # macOS
ls -ln <path>
```

Compare the numeric owner against the account behind `local-node1-goal-watch.timer` and the account reading the file, then `chown` to the intended owner. Do not relax `0o600`. Check parent directory ownership as well.

Phases 1 and 3 are independent — the audit fix does not block on the reinstall.

### Phase 4 — re-run with mandatory evidence

Restart the read-only Node1 goal and require both passing test output and a successful audit-file read as completion artifacts, so a repeat fails loudly instead of stalling silently at iteration 3.

---

## 6. Candidate hardening — not implemented, needs a decision

### 6a. Missing root `conftest.py` (verified reproducible)

There is no root `conftest.py` and no `tests/__init__.py`. Bare `pytest` from the repo root fails:

```
pytest tests/test_managed_route_contract.py
E   ModuleNotFoundError: No module named 'agentic_harness'
```

CI only works because it runs `python -m pip install -e ".[test]"` first. **Any Node1 runner invoking bare `pytest` without the editable install fails to import** — a second, independent path to an import error that superficially resembles the §2 contract mismatch and would be misdiagnosed as one.

Proposed fix: add a root `conftest.py` making the suite importable without a prior editable install, plus a test asserting the package resolves from the repo root. Small, additive, and closes the failure mode permanently.

### 6b. No future-timestamp guard (verified absent)

The thread reported a worker writing a completion timestamp four minutes in the future.

Timestamps generated *by this repo* come from the system clock (`agentic_harness/core/tournament.py:301`, `agentic_harness/core/workspace_transaction.py:136`) and cannot land in the future. But there is **no guard validating a worker-supplied timestamp against wall-clock anywhere in `agentic_harness/core/`**. If a worker self-reports its completion time, nothing rejects an implausible value.

Worth deciding on: reject or flag worker-supplied completion times beyond a small skew tolerance.

### 6c. Diff-accounting mislabel — not in this repo

No `is_new`, `new_file`, `added_lines`, or `lines_added` symbols exist anywhere in the codebase. That accounting lives in Node1 tooling, same as the audit JSON. Not actionable here.

---

## 7. What this session could not access

Stated so nothing below is mistaken for checked:

- `<controller-home>` on the Mac — this container is Linux and has no such path
- `<node1-host>` on the private tailnet — does not resolve; no `tailscale` CLI; the container is not a tailnet member
- Run directory `20260731T200851Z` and all of its receipts
- The 9 regression tests and the worker's diff
- Node1 install state, timer state, and vLLM health

The Node1 documentation tree was never touched. All Node1 and controller-Mac steps above are proposals, not actions taken.

---

## 8. File and line quick reference

| Subject | Location |
|---|---|
| `_managed_workspace_identity` definition | `agentic_harness/gui/server.py:1533` |
| Production use of it | `agentic_harness/gui/server.py:172-187` |
| Test import | `tests/test_managed_route_contract.py:23-28` |
| Test use | `tests/test_managed_route_contract.py:1858,1881-1882` |
| Introducing commit | `a1d0413` (0.13.5 → 0.13.6) |
| CI install and test contract | `tests/test_ci_workflow.py:102,129` |
| Allowed paths rendered as ticket prose | `agentic_harness/core/local_goal_bridge.py:1029,1070` |
| Allowed paths wiring | `agentic_harness/core/local_goal_bridge.py:528,539,561,575` |
| Embedded permission wiring | `agentic_harness/gui/backend.py:1135,1532` |
| Completion timestamp generation | `core/tournament.py:301`, `core/workspace_transaction.py:136` |
| Restrictive-mode writes | `core/events.py:58`, `core/tournament.py:277`, `core/config.py:396`, `gui/server.py:1615` |
