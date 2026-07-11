# Security Policy

Agentic Harness executes tools, reads and writes project files, stores run
evidence, and can connect to user-selected model endpoints. Security reports
that could affect workspace containment, credentials, review integrity, the
loopback GUI, or the release pipeline are taken seriously.

## Supported versions

Security fixes are made against the latest published release and the current
`main` branch. Earlier pre-1.0 releases are not maintained as separate security
support lines. Reproduce a report on the latest release when practical, and
include the exact version tested.

## Report a vulnerability privately

Do not include exploit details, credentials, private repository content, or a
working proof of concept in a public issue.

Use GitHub's enabled private vulnerability-reporting form:

<https://github.com/moortekweb-art/agentic-harness/security/advisories/new>

If GitHub is temporarily unable to offer that form, open a minimal public issue titled
`Private security report channel needed`. State only that you need a private
channel; do not describe the vulnerability. A maintainer can then arrange a
private report path before technical details are shared.

Include the following in the private report:

- the affected Agentic Harness version or commit;
- the operating system, Python version, interface, and worker type involved;
- the security boundary that was crossed and the resulting impact;
- minimal reproduction steps using synthetic data;
- whether credentials, external services, or user interaction are required;
- any known mitigations or suggested fix; and
- your preferred disclosure and credit details.

Never send live API keys, access tokens, private model prompts, or proprietary
source. Replace them with clearly marked test values.

## Response and disclosure

The project does not promise a fixed response SLA. Maintainers will acknowledge
complete reports and provide an initial assessment as soon as practical;
complex reports may take longer to reproduce. Please allow a reasonable
remediation period and coordinate public disclosure so users can update first.

Confirmed fixes will be tested against the affected boundary and documented in
release notes. A GitHub security advisory and CVE may be used when the impact
warrants them.

## In scope

Examples include:

- workspace, symlink, protected-path, or pre-existing-change containment bypasses;
- plaintext credential persistence or credential disclosure through logs,
  events, reports, URLs, browser storage, or review commands;
- GUI authentication, host validation, origin, or non-loopback exposure flaws;
- acceptance of an unfinished or failed goal as independently verified;
- command, argument, or configuration injection across a documented trust boundary;
- release identity, artifact, trusted-publishing, or immutable-tag bypasses; and
- unsafe file permissions or state corruption that exposes another user's data.

Reports about an explicitly configured external coding agent exercising the
permissions granted to that agent are normally outside the embedded engine's
security boundary. They are still useful when Agentic Harness misrepresents or
bypasses its documented adapter boundary.

## Good-faith research

Use only systems and data you own or are authorized to test. Avoid privacy
violations, persistence, destructive actions, service disruption, and access
beyond what is needed to demonstrate the issue. The project welcomes
good-faith reports that follow these limits.
