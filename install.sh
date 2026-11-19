#!/bin/sh
# Install the toolkit's commands, skills, presets and prompts into an OpenCode config folder.
# Anything it replaces is copied to a unique backups/<time>-pre-toolkit.* directory first.
# Use --dry-run to see what would change.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$HERE/commands/.toolkit.sh"
TARGET=${OPENCODE_CONFIG_DIR:-$HOME/.config/opencode}
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1
BACKUP_ROOT="$TARGET/backups"
BACKUP=
CHANGED=0

copy() { # copy <source> <destination>: skip when identical, back up before replacing
  src=$1; dst=$2
  toolkit_no_links "$dst"
  if [ -e "$dst" ]; then
    if diff -rq "$src" "$dst" >/dev/null 2>&1; then return 0; fi
    printf 'update  %s\n' "$dst"
    if [ "$DRY" -eq 0 ]; then
      rel=${dst#"$TARGET"/}
      if [ -z "$BACKUP" ]; then
        toolkit_no_links "$BACKUP_ROOT"
        mkdir -p "$BACKUP_ROOT"
        BACKUP=$(mktemp -d "$BACKUP_ROOT/$(date +%Y-%m-%d-%H%M%S)-pre-toolkit.XXXXXXXX")
      fi
      toolkit_no_links "$BACKUP/$rel"
      mkdir -p "$BACKUP/$(dirname "$rel")"
      cp -Rp "$dst" "$BACKUP/$rel"
    fi
  else
    printf 'add     %s\n' "$dst"
  fi
  CHANGED=$((CHANGED + 1))
  [ "$DRY" -eq 1 ] && return 0
  mkdir -p "$(dirname "$dst")"
  if [ -d "$src" ]; then rm -rf "$dst"; cp -Rp "$src" "$dst"; else cp -p "$src" "$dst"; fi
}

# Refuse an unsafe command path before installing any command.
for name in $(toolkit_commands); do
  toolkit_regular_or_missing "$HERE/commands/$name.md"
  toolkit_regular_or_missing "$TARGET/commands/$name.md"
  if [ ! -f "$HERE/commands/$name.md" ]; then
    printf 'missing toolkit command: %s\n' "$name" >&2
    exit 1
  fi
done
for name in $(toolkit_commands); do
  copy "$HERE/commands/$name.md" "$TARGET/commands/$name.md"
done
for skill in "$HERE"/skills/*/; do
  name=$(basename "$skill")
  [ "$name" = "licenses" ] && continue
  copy "${skill%/}" "$TARGET/skills/$name"
done
copy "$HERE/config/oh-my-opencode-slim.json" "$TARGET/oh-my-opencode-slim.json"
copy "$HERE/config/oh-my-opencode-slim" "$TARGET/oh-my-opencode-slim"
# AGENTS.md often holds your own notes too, so an existing one is never replaced.
if [ -e "$TARGET/AGENTS.md" ] && ! diff -q "$HERE/config/AGENTS.md" "$TARGET/AGENTS.md" >/dev/null 2>&1; then
  copy "$HERE/config/AGENTS.md" "$TARGET/AGENTS.toolkit.md"
  printf 'note    AGENTS.md exists and differs: the toolkit version is in AGENTS.toolkit.md; merge it by hand\n'
else
  copy "$HERE/config/AGENTS.md" "$TARGET/AGENTS.md"
fi

if [ "$CHANGED" -eq 0 ]; then echo "Nothing to change."
elif [ "$DRY" -eq 1 ]; then echo "$CHANGED item(s) would change. Nothing was written."
else
  echo "$CHANGED item(s) installed."
  if [ -n "$BACKUP" ]; then echo "Replaced files are in $BACKUP."; fi
  echo "See config/PLUGINS.md to turn the visualizer on, then reload OpenCode."
fi
