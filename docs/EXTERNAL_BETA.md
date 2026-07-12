# External beta guide

This beta is for people willing to try Agentic Harness on a real repository and
report setup failures as well as successes. Do not test on irreplaceable work;
use a clean branch or disposable clone and review every diff.

## Ten-minute path

```bash
pipx install local-agentic-harness==0.7.3
cd /path/to/your/project
agentic-harness init-agent codex
agentic-harness do "make one small, reviewable change" --check "YOUR EXISTING CHECK"
agentic-harness report
```

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
