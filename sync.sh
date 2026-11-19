#!/bin/sh
# Copy the installed commands, skills, presets and prompts back into this repository, so a change the
# setup made to itself (an approved prompt or skill edit) can be reviewed with git diff and committed.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$HERE/commands/.toolkit.sh"
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
# Skills kept out of the toolkit on purpose (personal or employer-specific), space separated.
SEPARATE=${TOOLKIT_SEPARATE_SKILLS:-shopify-ruby}
set -- --exclude __pycache__ --exclude .DS_Store --exclude OPENCODE-CHANGES.md --exclude AUDIT.md --exclude THIRD-PARTY-NOTICES.md --exclude licenses --exclude ui-ux-pro-max
for name in $SEPARATE; do set -- "$@" --exclude "$name"; done
rsync -a --delete "$@" "$SOURCE/skills/" "$HERE/skills/"
cp -p "$SOURCE/oh-my-opencode-slim.json" "$HERE/config/oh-my-opencode-slim.json"
rsync -a --delete "$SOURCE/oh-my-opencode-slim" "$HERE/config/"
# Only the orchestration rules: everything from the first personal heading on stays out.
awk '/^# Muse 2/ { exit } { print }' "$SOURCE/AGENTS.md" > "$HERE/config/AGENTS.md"
git -C "$HERE" status --short -- commands skills config | head -40
