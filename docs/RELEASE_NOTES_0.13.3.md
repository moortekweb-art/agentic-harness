# Agentic Harness v0.13.3

Version 0.13.3 makes managed model routing explicit and verifiable.

- Adds canonical route identities for local build, Turnstone GLM plus local build,
  cloud GLM build, and direct GLM read-only audit.
- Rejects stale or mismatched route identities before external dispatch.
- Adds optional GLM-5.2 advisory supervision for local builds, including live
  start/status verification and rollback when task start fails.
- Enables the distinct GLM read-only audit contract while keeping direct GLM
  implementation blocked.
- Preserves MiniMax as the generic cloud automation default and Kimi as its
  bounded fallback; explicit GLM routes remain separately pinned.
