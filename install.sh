#!/bin/sh
# Install or update the toolkit's commands, skills, presets and prompts in an OpenCode config folder.
#
#   sh install.sh [--dry-run]        install, or update without losing your own changes
#   sh install.sh --list-backups     the backups earlier runs made
#   sh install.sh --restore NAME [--dry-run]   put a backup's files back
#
# Files you never changed are updated. Files you changed are kept and the toolkit's version is written beside
# them as <name>.toolkit-new. Files you created are never touched. oh-my-opencode-slim.json is merged: your
# chosen preset, models and added agents stay. Anything replaced is copied to backups/<time>-pre-toolkit.* first.
# The work is done by agentgraph/installer.py; docs/CUSTOMIZE.md explains it.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$HERE/commands/.toolkit.sh"
TARGET=${OPENCODE_CONFIG_DIR:-$HOME/.config/opencode}
usage() { echo "usage: sh install.sh [--dry-run] | --list-backups | --restore NAME [--dry-run]" >&2; exit 2; }

MODE=install
DRY=
BACKUP=
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=--dry-run ;;
    --list-backups) MODE=list-backups ;;
    --restore) MODE=restore; [ $# -ge 2 ] || usage; BACKUP=$2; shift ;;
    *) usage ;;
  esac
  shift
done
command -v python3 >/dev/null 2>&1 || { echo "install.sh needs python3" >&2; exit 1; }
COMMANDS=$(toolkit_commands | tr '\n' ',')
set -- "$HERE/agentgraph/installer.py" "$MODE" --target "$TARGET" --source "$HERE" --commands "$COMMANDS"
[ -n "$DRY" ] && set -- "$@" "$DRY"
[ -n "$BACKUP" ] && set -- "$@" --backup "$BACKUP"
exec python3 "$@"
