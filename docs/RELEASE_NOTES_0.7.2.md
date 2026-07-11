# Agentic Harness v0.7.2

This immutable corrective release repairs the representative benchmark receipt
provenance defect in the public v0.7.1 sdist. Runtime behavior is unchanged.

## Fixed

- The protected tag build now regenerates the representative 24-case,
  48-record benchmark before packaging.
- Benchmark provenance is captured from the clean release baseline before
  generated result files change, and the harness version comes from the source
  package metadata.
- Publication fails unless the receipt names the exact release commit and
  version, reports a clean baseline, matches all nine packaged source files,
  and has internally consistent aggregate and raw record counts.

The v0.7.1 tag and artifacts remain unchanged.
