# Controller operations

> OpenCode note: this file describes the Codex toolkit's separate-session controller and its "spawn only when asked" stance. In this setup, follow `AGENTS.md` and the orchestrator prompt instead (task graph, loop items, verifier and reviewer); read this file as background only.

Run `python -B scripts/controller.py --help` from this skill folder. `version`,
`route`, `discover`, `assess` and `doctor` make no model calls. An explicit
`doctor --codex /absolute/trusted/codex` performs only a bounded version probe
after checking the executable and its parent chain. Do not chmod or bypass a
trust failure. Selecting an existing verified actual binary is supported.

## Session lifecycle

1. `init --state /absolute/private/session --workspace /absolute/repo --model
   <selected-model> --effort <selected-effort>` creates state outside the workspace.
   It returns an owner capability once; only its digest is stored.
2. `preview --state ... --proposal packet.json --owner-stdin` creates an expiring
   exact-state preview. Optional `--skill <entrypoint>` supplies relevant guidance;
   `--worker-effort` is an explicit preset override.
   It reports the serialized worker-packet character/byte budget. Optional
   `context`, `depends_on` and `scope.resources` are described in
   [context and concurrency](context-and-concurrency.md). Runtime previews also
   reject conflicting active scopes; the policy-only `assess` command cannot
   establish that check from its smaller ledger.
3. `reserve --state ... --token <preview-token> --approval approve:<digest>
   --owner-stdin` consumes the preview once and durably reserves a pending slot.
4. `launch --state ... --id <launch-id> --codex /absolute/trusted/codex
   --account-home /absolute/account-home --owner-stdin` attempts one foreground
   execution, with a default 300-second timeout. It never retries or resumes.
5. Inspect source, checks and `run-<launch-id>/handoff.txt`. `finish --state ...
   --id ... --accepted true|false --verification <evidence> --owner-stdin` records
   the coordinator's decision and observed usage. Completion alone keeps the slot
   occupied until reviewed.

If the installed Codex wrapper uses `env node`, Node remains an external
prerequisite. Add `--node /absolute/trusted/node` to `launch` when it is outside
the fixed system PATH. The selected file must be named `node`; its file and
parent chain receive the same trust checks as Codex and are rechecked before
spawn. Only its verified parent is added to the sanitized PATH; user PATH and
Node loader options are not inherited. No Node download or symlink bypass is
performed. The version-only doctor probe does not accept this launch option.

Pass the owner capability on stdin, never in command arguments or worker packets.
It guards the API against accidental or unauthenticated callers, not hostile
processes sharing the OS user. The controller does not execute the acceptance
string: the coordinator must assess real verification evidence.

`status --state ...` shows phases, receipts and usage. `reconcile --state ...
--id ... --owner-stdin` recovers a matching durable supervisor receipt without
relaunching. Without a receipt, the slot remains unresolved. Never infer a safe
retry from a reusable PID or blindly reset a ledger.

## Account and artifact handling

A private `CODEX_HOME` retains the selected provider with external tools disabled.
Simple file/environment auth and validated absolute command helpers are supported
contracts. Codex invokes a configured helper; the controller does not invoke it
or read its stdout token. Command auth preserves the validated user HOME because
organization helpers can depend on it. Optional environment headers are copied
only when present. Keyring-only credentials and unsafe/unknown auth forms fail
closed. One bounded macOS account session completed; other accounts and hosts
still need a reviewed live smoke, and effective permissions remain uncertified.
A connection smoke does not establish gateway identity, account entitlement or
session routing; verify those against provider/session evidence separately.

After completion, timeout or handled failure, descriptor-relative cleanup removes
the worker home, copied credentials, config and CLI history. By default, raw events
and stderr are discarded and receipts retain hashes. The Python adapter accepts
`retain_diagnostics=True` for explicit local diagnostics; benchmark v2 uses it to
keep redacted JSONL and stderr outside the solver workspace. These files can still
contain private source and must not be published automatically. The bounded
handoff redacts known credential values but is still potentially sensitive, untrusted task output.
Power loss or a hard kill can leave private artifacts: inspect and clean them
after confirming the worker stopped. Do not advertise crash-proof cleanup.

The event adapter supports the observed Codex CLI **0.163.0** completion shape.
Ledger usage contains only `input_tokens`, `cached_input_tokens` and
`output_tokens`; observed totals add input and output once. Optional
`cache_write_input_tokens` and `reasoning_output_tokens` are validated bounded
nonnegative integers and retained separately as `usage_details` in the receipt.
Cache reads plus cache writes must fit input tokens; reasoning must fit output
tokens. Runtime acceptance revalidates supplied details as well as base counters.
Absent detail fields stay absent. Unknown or malformed counters fail closed;
duplicate thread/completion events remain errors. Protocol failures retain observed
usage records and diagnostic reasons without treating ambiguous records as totals.
Receipt duration starts before launch preparation; preparation and execution
durations are also separate. The adapter accepts finite deadlines up to 2,400
seconds, including preparation. This is an observed protocol
compatibility point, not a guarantee for future CLI versions.

## Reviewed configuration and optional profiles

`config-preview --state ... --set reviewer.model=<model>` produces a ten-minute
preview. `config-apply --state ... --token ... --approval approve:<digest>` applies
it against unchanged state. Existing config receives an exact private backup.
`config-restore-preview --state ... --backup config-backup-<sha256>.json` previews
restoration; apply with `config-apply`.

`profile-preview` / `profile-apply` require an explicit `--profiles-root` and
`--action install|update|uninstall`. They manage four optional prompt exports,
receipts and backups while preserving foreign files. They neither register a
plugin nor establish permissions. Normal skill copying does not run these commands.

`python -B scripts/verify.py` runs offline scratch-home tests. POSIX mutation tests
are skipped on native Windows; that is an explicit support boundary, not proof
of a Windows execution runtime.

## Offline comparison

`python -B scripts/benchmark.py --repo /path/to/toolkit --baseline-ref <commit>
--candidate-ref <commit> --output /path/to/results` compares two committed skill
versions in temporary directories without model calls or global changes. It
records selected admission scenarios, minimal prompt size and CPU timings in
Markdown and JSON. Git and the requested local commits are required. New schemas
unsupported by the baseline are reported separately; the score is targeted
control coverage, not task-success probability, token savings or sandbox proof.

## Optional delegated cycle

The `ReviewCycle` Python API combines worker acceptance and the next action in
one coordinator response. It is opt-in and does not change these CLI commands.
See [bounded review cycle](review-cycle.md) before connecting a trusted metered
host. Its deterministic replay is separate from the scored benchmark.
