# This allowlist is shared by installation, sync, and the command checker.
toolkit_commands() {
  printf '%s\n' agent-skills flow flows graph-check learn note spend
}

toolkit_no_links() {
  case "$1" in ''|.|/) return 0 ;; esac
  if [ -L "$1" ]; then
    printf 'refusing symlink: %s\n' "$1" >&2
    return 1
  fi
  toolkit_no_links "$(dirname -- "$1")"
}

toolkit_regular_or_missing() {
  toolkit_no_links "$1" || return 1
  if [ -e "$1" ] && [ ! -f "$1" ]; then
    printf 'refusing non-regular file: %s\n' "$1" >&2
    return 1
  fi
}
