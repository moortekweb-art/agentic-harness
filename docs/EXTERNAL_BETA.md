# External beta guide

This beta is for people willing to try Agentic Harness on a real repository and
report setup failures as well as successes. Do not test on irreplaceable work;
use a clean branch or disposable clone and review every diff.

The v0.12 readiness gate requires at least five submitted attempts from at
least two non-maintainer participants across three repository ecosystems.
Failed and abandoned onboarding attempts count and must not be discarded. At
least 80% of attempts (and at least four) must reach a verified result without
maintainer intervention, with zero credential leaks, unsafe unexpected writes,
false verified completions, or unresolved critical/high defects.

## Ten-minute path

Browser-first novice path:

```bash
pipx install local-agentic-harness==0.12.0
cd /path/to/your/project
agentic-harness gui
```

Without terminal help, open Settings, connect an installed coding app or
detected local AI, return Home, describe one small task, and reach a verified
result. Record whether you had to type a path, command, endpoint, or model ID.

Terminal comparison path:

```bash
pipx install local-agentic-harness==0.12.0
cd /path/to/your/project
agentic-harness init-agent codex
agentic-harness do "make one small, reviewable change" --check "YOUR EXISTING CHECK"
agentic-harness report
```

This guide deliberately exercises the immutable v0.12.0 release used by the
current readiness program. For ordinary use after the beta, install the latest
published release with `pipx install local-agentic-harness`.

Use a deterministic command the project already trusts, such as a focused test,
lint command, or build. Do not weaken the command merely to obtain a pass.

## What to return

Copy `docs/EXTERNAL_BETA_FEEDBACK.md` and fill it in once per attempted
repository—including abandoned or failed onboarding. Remove credentials,
private source, personal paths, and proprietary output. The useful measures are
time to first verified result, assistance required, terminal category, retries,
confusing steps, and whether you would use the tool again.

Submit sanitized feedback through a GitHub issue in this repository. If the
report cannot be made public safely, keep it local rather than disclosing
private project material.

Maintainers recording a private sanitized receipt can use
`evaluation/external_beta_receipt.example.json` as the machine-readable shape.
Store real receipts outside the repository until participants consent to
publication, then summarize them without inventing or excluding attempts:

```bash
python evaluation/summarize_external_beta.py \
  /path/to/sanitized-receipts \
  --output /tmp/agentic-harness-v012-beta-summary.json
```
