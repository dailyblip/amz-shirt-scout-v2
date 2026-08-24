# Repo Steward policy

Repo Steward watches completed GitHub Actions workflows in this repository.

- One automatic retry is allowed for a likely transient infrastructure or upstream-service failure.
- A code or configuration repair must be minimal, bounded, and validated.
- Repairs are proposed through pull requests and are never auto-merged.
- Research data, ledgers, generated feeds, and other outputs must never be fabricated or rewritten merely to make a job pass.
- Missing credentials, external outages, rate limits, bad upstream data, and ambiguous failures are diagnosed but not patched around.
- Agent artifacts are retained briefly for auditability; an incident issue remains open until the run recovers or a human resolves it.
