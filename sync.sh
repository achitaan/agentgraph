#!/bin/sh
# Copy the installed skills, presets and prompts back into this repository, so a change the
# setup made to itself (an approved prompt or skill edit) can be reviewed with git diff and committed.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SOURCE=${OPENCODE_CONFIG_DIR:-$HOME/.config/opencode}
# Skills kept out of the toolkit on purpose (personal or employer-specific), space separated.
SEPARATE=${TOOLKIT_SEPARATE_SKILLS:-shopify-ruby}
set -- --exclude __pycache__ --exclude .DS_Store --exclude OPENCODE-CHANGES.md --exclude AUDIT.md --exclude THIRD-PARTY-NOTICES.md --exclude licenses --exclude ui-ux-pro-max
for name in $SEPARATE; do set -- "$@" --exclude "$name"; done
rsync -a --delete "$@" "$SOURCE/skills/" "$HERE/skills/"
cp -p "$SOURCE/oh-my-opencode-slim.json" "$HERE/config/oh-my-opencode-slim.json"
rsync -a --delete "$SOURCE/oh-my-opencode-slim" "$HERE/config/"
# Only the orchestration rules: everything from the first personal heading on stays out.
awk '/^# Muse 2/ { exit } { print }' "$SOURCE/AGENTS.md" > "$HERE/config/AGENTS.md"
git -C "$HERE" status --short -- skills config | head -40
