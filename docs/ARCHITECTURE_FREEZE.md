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
