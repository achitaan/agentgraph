# Orchestrator policy source

`orchestrator_append.src.md` is the one source of `oh-my-opencode-slim/<preset>/orchestrator_append.md` for the
presets `fast`, `normal` and `ultra-fast`. Shared text is written once; what differs is in `<!-- only: ... -->`
blocks closed by `<!-- end -->`. Markers and commands: the top of `agentgraph/policygen.py`.

This folder is not under `config/oh-my-opencode-slim/`, so `install.sh` does not copy it and `sync.sh`'s
`rsync --delete` of that folder does not remove it.

    python3 -m agentgraph.policygen build --out DIR   # writes DIR/<preset>/orchestrator_append.md
    python3 -m agentgraph.policygen check             # compare with the live files
    python3 -m agentgraph.policygen stats
