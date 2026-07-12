# Representative v0.7.2 Release Snapshot

The generated data files `environment.json`, `raw.jsonl`, `summary.json`, and
`summary.md` are an immutable v0.7.2 release snapshot of the controlled
completion-gate evaluation. Those four files were copied byte-for-byte from the
public `local-agentic-harness` 0.7.2 PyPI sdist after its SHA-256 digest was
verified as:

```text
6dfd2240a39fa9b0199cd52f7096088dcafaad85d3476e30fa49474275384c35
```

The snapshot is bound to the v0.7.2 tag commit:

```text
751aead465edbdd09c2a93cc2162164c70a998ce
```

Validate against that tag commit, not current main. The source checksums may
correctly differ from a default branch that contains later changes.

The snapshot contains 24 scripted task cases and 48 arm records. It measures
completion-gate rejection and recovery behavior; it is not a real-model
benchmark or an adoption claim.

Run new comparisons into a separate output directory. Do not overwrite these
release-bound files with results from another source revision.
