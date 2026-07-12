# Published-package terminal demo — 2026-07-12

This terminal recording starts from a clean virtual environment, installs
`local-agentic-harness==0.7.3` from PyPI, and runs the packaged `fix-tests`
demo. The worker falsely claims completion on attempt one; independent pytest
verification rejects it; attempt two repairs the project and passes.

- Wall time: 8.61 seconds.
- Result: Verified done.
- Attempts: 2.
- Retries: 1.
- Review: passed.

Replay `terminal.typescript` with the event delays in `terminal.timing` using a
compatible `scriptreplay` implementation. Local `/tmp` paths are part of the
ephemeral demonstration and contain no credentials.
