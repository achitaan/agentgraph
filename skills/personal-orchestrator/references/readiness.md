# Runtime boundaries

> OpenCode note: this file describes the Codex toolkit's separate-session controller and its "spawn only when asked" stance. In this setup, follow `AGENTS.md` and the orchestrator prompt instead (task graph, loop items, verifier and reviewer); read this file as background only.

| Capability | Current evidence |
|---|---|
| Copied skill and CLI | Self-contained Python 3.11+ source; no Lane installation |
| Policy, routing and metadata parsing | Portable deterministic tests; no automatic model substitution |
| Filesystem metadata discovery | Requires no-follow file reads; native Windows reports the unsupported capability without opening metadata |
| Previews, receipts and backups | Recovered/adapted tests in scratch state |
| Ownership, admission, budgets, recovery | Fake-worker tests; one active worker by default |
| Accepted prerequisites and concurrent scope checks | Deterministic policy/runtime tests; declared paths/resources only, not containment |
| Compact context and packet size | Bounded input and exact character/byte accounting; no tokenizer or source-freshness certification |
| Separate-session restrictions | Command/config tests and a macOS denied-write probe; broad containment remains uncertified |
| Project/ancestor overrides | Launch blocks conflicting Codex config; strict-config alone is not isolation |
| macOS | Two completed CLI benchmark studies; host routing and bounded write-denial/cancellation probes; strict containment remains uncertified |
| Linux/WSL | POSIX implementation; separate environment validation pending |
| Native Windows | Skill copying, policy, routing and metadata parsing; descriptor-dependent discovery reads, mutation and execution unsupported |
| Organization command auth | Current-account CLI calls completed through the approved host path; host-selected routing observed, provider-served identity remains unknown |
| Node-based Codex wrappers | Explicit trusted `launch --node` path; fake env-node startup tested; no inherited user PATH |
| Completion events | Observed Codex CLI 0.163.0 shape tested with synthetic events; optional usage details do not inflate ledger totals |
| Safe resume | Unsupported; no exec-resume path |
| Descendant delegation | Native agents disabled; arbitrary shell recursion is not hard-contained |
| Token savings/model quality | Eight-task repeated comparisons found no established improvement at similar tokens; forced delegation was costly. See benchmark reports |

Custom agents inherit permissions, provider and MCP access; profile files do not
create a separate read-only boundary. See the official
[custom-agent reference](https://learn.chatgpt.com/docs/agent-configuration/subagents).
Receipts therefore label sandbox/model settings as requested and live enforcement
as unverified. Do not convert fake-worker results into production claims.

The owned controller's filesystem discovery reader requires `O_NOFOLLOW` and
refuses access when it is unavailable. This limitation does not affect ordinary
Codex skill discovery or toolkit skill installation on Windows. Parser and
no-follow refusal tests run on every platform; only integration cases that need
the descriptor capability are conditional. Controller mutation remains POSIX-only.

A successful model response proves a connection smoke only. Confirming the
intended gateway, account, requested model and session routing needs separate
provider/session evidence; copied headers or a completed response do not prove
that mapping. Keep this evidence distinct from sandbox and tool restrictions.

State ownership protects other-user access and accidental API misuse, not an
arbitrary hostile same-user process. Preserving HOME for organization auth also
preserves access to that user's account context; the separate CLI sandbox must
be validated live. Known-value redaction cannot prove all output is secret-free.

The preset effort table records the host tool catalog observed on 2026-10-25,
not account entitlement. Missing models, incompatible effort, unsafe executable
or helper paths, unsupported auth, and unknown usage under a token threshold
remain visible failures. No silent provider or model fallback is permitted.

## Benchmark evidence (2026-10-27)

Both A–D and E–H completed 64 main cells and independent source review through
one macOS CLI account. The first restricted-shell smoke failed at organization
auth; its unknown usage remains in the ledger. Later calls used the approved host
path. No authentication or trust checks were bypassed.

Host session records establish the requested model selection, not the provider's
served identity or actual billing. Read-only write denial and process-group
cancellation were probed separately; arbitrary shell recursion, hostile same-user
isolation, server-side spend enforcement, Windows/WSL execution and native desktop
delegation remain unverified. Metadata retains the conservative broad live flag.

The tested adapter is 0.2.2. Results and later post-review audits are under
`docs/benchmarks/workflow-eval-v2/` and `workflow-patterns-v1/` in the toolkit.
Keep original grades separate from derived audits and future controller revisions.
No global installed skill was updated by these experiments.

## Optional review-cycle revisions

The 0.3.0 source at Git commit `f5fd787` was tested on sixteen original live cells:
none completed its workflow, eight passed the narrow behavioral checks, and five
saved outputs were adequate in the final common review. Mean quality was 3.625,
versus 5.75 for the matched old-D re-review. The later-batch comparison has missing
usage and service-interruption differences; it does not establish causal effects
or savings. See `docs/benchmarks/workflow-d-revision-v1/` in the toolkit. The 0.2.2
source remains at `b7ca0f9`; frozen reports and evaluated source are unchanged.

Version 0.4.0 adds bounded raw-JSON recovery, correction of invalid reviews,
role-specific guidance, original-contract propagation and receipt-detail checks.
Only offline regression tests cover this version. Reviewers remain read-only;
checks requiring scratch writes need host preflight and cannot become passes when
unavailable. A trusted coordinator still judges whether findings match the task.
No live quality, token, price or latency improvement has been established. Global
installed skills, account routing and existing CLI defaults are unchanged. Read
[the API contract](review-cycle.md) before selecting the optional cycle.
