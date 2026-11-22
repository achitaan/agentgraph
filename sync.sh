#!/bin/sh
# Copy the installed commands, skills, presets and prompts back into this repository, so a change the
# setup made to itself (an approved prompt or skill edit) can be reviewed with git diff and committed.
# What is personal to you (skills kept out of the toolkit, where your own notes start in AGENTS.md) comes from
# sync.conf, which git ignores; copy sync.conf.example to start one.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$HERE/commands/.toolkit.sh"
SEPARATE_SKILLS=
AGENTS_CUT_MARKER=
if [ -f "$HERE/sync.conf" ]; then . "$HERE/sync.conf"; fi
SOURCE=${OPENCODE_CONFIG_DIR:-$HOME/.config/opencode}
BACKUP_ROOT=${TOOLKIT_SYNC_BACKUP_ROOT:-$SOURCE/backups}
BACKUP=
sync_file() {
  rel=$1
  src=$SOURCE/$rel
  dst=$HERE/$rel
  toolkit_regular_or_missing "$src"
  toolkit_regular_or_missing "$dst"
  [ -f "$src" ] || return 0
  if [ -f "$dst" ]; then
    cmp -s "$src" "$dst" && return 0
    if [ -z "$BACKUP" ]; then
      toolkit_no_links "$BACKUP_ROOT"
      mkdir -p "$BACKUP_ROOT"
      BACKUP=$(mktemp -d "$BACKUP_ROOT/$(date +%Y-%m-%d-%H%M%S)-pre-sync.XXXXXXXX")
    fi
    mkdir -p "$BACKUP/$(dirname -- "$rel")"
    cp -p "$dst" "$BACKUP/$rel"
  fi
  mkdir -p "$(dirname -- "$dst")"
  cp -p "$src" "$dst"
}
sync_commands() {
  for name in $(toolkit_commands); do
    toolkit_regular_or_missing "$SOURCE/commands/$name.md"
    toolkit_regular_or_missing "$HERE/commands/$name.md"
  done
  for name in $(toolkit_commands); do
    sync_file "commands/$name.md"
  done
}
if [ "${1:-}" = "--commands-and-learning" ]; then
  sync_commands
  for name in improve-orchestration improve-orchestration-2; do
    sync_file "skills/$name/SKILL.md"
  done
  echo "Synced commands and both improvement skills."
  exit 0
fi
sync_commands
# Skills kept out of the toolkit on purpose (personal or employer-specific), space separated (sync.conf).
SEPARATE=${TOOLKIT_SEPARATE_SKILLS:-$SEPARATE_SKILLS}
set -- --exclude __pycache__ --exclude .DS_Store --exclude OPENCODE-CHANGES.md --exclude AUDIT.md --exclude THIRD-PARTY-NOTICES.md --exclude licenses --exclude ui-ux-pro-max
for name in $SEPARATE; do set -- "$@" --exclude "$name"; done
rsync -a --delete "$@" "$SOURCE/skills/" "$HERE/skills/"
cp -p "$SOURCE/oh-my-opencode-slim.json" "$HERE/config/oh-my-opencode-slim.json"
rsync -a --delete "$SOURCE/oh-my-opencode-slim" "$HERE/config/"
# The three orchestrator policy files have one source (config/policy/). When an approved edit changed a live file
# directly, make the source again from the live files, so it never falls behind and the edit shows in git diff.
if ! (cd "$HERE" && python3 -m agentgraph.policygen check --against "$HERE/config/oh-my-opencode-slim" >/dev/null 2>&1); then
  (cd "$HERE" && python3 -m agentgraph.policygen split --from "$HERE/config/oh-my-opencode-slim" --to "$HERE/config/policy/orchestrator_append.src.md" --force >/dev/null)
  echo "note: a live policy file differed from config/policy/orchestrator_append.src.md, so the source was made again from the live files"
fi
# Only the orchestration rules: everything from the first line starting with AGENTS_CUT_MARKER (sync.conf) stays
# out. Without a marker the file is left alone, so your own notes never land in the repository by accident.
if [ -n "$AGENTS_CUT_MARKER" ]; then
  awk -v marker="$AGENTS_CUT_MARKER" 'index($0, marker) == 1 { exit } { print }' "$SOURCE/AGENTS.md" > "$HERE/config/AGENTS.md"
else
  echo "note: AGENTS_CUT_MARKER is not set in sync.conf, so config/AGENTS.md was not synced"
fi
git -C "$HERE" status --short -- commands skills config | head -40
