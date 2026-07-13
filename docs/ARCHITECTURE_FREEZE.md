# Architecture freeze

The project is in evidence-gathering mode after v0.7.3. Until external use
identifies a concrete blocker, do not add another agent adapter, provider,
orchestration mode, dashboard surface, or deployment framework.

Allowed work is limited to defects, security fixes, compatibility maintenance,
evaluation reproducibility, onboarding clarity, and changes directly supported
by observed user failures. Exceptions require a written problem statement,
evidence that the existing workflow cannot solve it, and an explicit decision.

The frozen product story is one workflow: run a project-local coding task,
preserve its evidence, and refuse accepted completion until an independent
command passes.

## Approved exception: public first-run strategy recovery

The public first-run work recorded in [PUBLIC_RELEASE.md](PUBLIC_RELEASE.md) is
an explicit exception. It responds to observed novice failures in which the four
intended approaches were hidden or coupled to an internal managed backend and
the GLM provider idea was mistaken for a product mode. The change does not add a
worker adapter or provider transport: it exposes four bounded policies over the
existing engine, adds editable non-secret provider templates, and preserves the
same independent completion gate.
