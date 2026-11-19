# Upstream capability parity

This is owned source, not a byte-for-byte port of every Lane release artifact.

| Upstream capability | Owned location/replacement | Status |
|---|---|---|
| security | controlplane/security.py | Recovered ownership, no-follow I/O, locks and atomic publication |
| plans | controlplane/plans.py | Recovered expiring previews, approval digests and single-use consumption |
| diagnostics | controlplane/diagnostics.py | Recovered structured diagnostics/rendering |
| models | controlplane/models.py | Four roles; inherited defaults |
| config | controlplane/config.py | Adapted validation/preview/apply; added backups/restoration |
| profiles | controlplane/profiles.py | Receipt/update/uninstall lifecycle; prompt-only owned exports |
| doctor | controlplane/doctor.py | Trusted bounded probes and owned metadata; no profile enforcement claims |
| discovery | controlplane/discovery.py | Bounded on-demand local discovery/ranking |
| routing | controlplane/routing.py | Reused signals; model-neutral direct/coordinated recommendations |
| usage | usage.py | Recovered accountant extended for running calls |
| adaptive | policy.py + runtime.py | Owned eligibility and compatible effort checks; no forced router call |
| orchestration | policy.py + runtime.py + execution.py | Packet checks, durable ownership, foreground execution, acceptance |
| cli / module entrypoint | scripts/controller.py + cli.py | Local CLI; kernel diagnostic retained |
| setup | Skill copying + explicit config/profile previews | Plugin/pack menu removed; no runtime downloader |
| benchmark and release scripts | Regression suite and fake-worker scenarios | Upstream scoring corpora and paid-model benchmarks not ported |
| voltagent and 172 specialist profiles | None | Deliberately excluded; selected skills supply expertise |
| plugin/marketplace manifests | None | Deliberately excluded; no registration dependency |

Code paths above are under `scripts/orchestrator/` unless explicitly stated.
The durable supervisor is new: upstream route cards were not a worker-launch
runtime. Safe resume, native Windows mutation and hard recursive-shell containment
remain unsupported. Published benchmark/quality/cost claims have not been inherited.
