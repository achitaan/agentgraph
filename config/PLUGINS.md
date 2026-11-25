# Turning the visualizer on

Clone this repository to `~/.config/opencode/orchestration` (the prompts and plugins expect that path), then add the two plugins.

**Desktop side panel.** In `~/.config/opencode/opencode.json`:

```json
"plugins": [
  { "package": "~/.config/opencode/orchestration/desktop-plugin",
    "options": { "python": "/absolute/path/to/python3" } }
]
```

**Terminal sidebar.** In `~/.config/opencode/cli.json`:

```json
"plugins": [
  { "package": "~/.config/opencode/orchestration/tui-plugin",
    "options": { "python": "/absolute/path/to/python3" } }
]
```

`python` must be Python 3.11 or newer, given as an absolute path (on Windows it may be left out: the plugins then find `py -3` or `python` themselves). Write the plugin paths out in full if your OpenCode version does not expand `~`.

On Windows, clone to `%USERPROFILE%\\.config\\opencode\\orchestration`, run `install.cmd` there, use the object form (not the legacy `[path, options]` tuple) and a
file URL such as `file:///C:/Users/NAME/.config/opencode/orchestration/tui-plugin` (the desktop plugin takes
`C:\\Users\\NAME\\.config\\opencode\\orchestration\\desktop-plugin`). Leave `options.python` out to let the plugin find Python, or give the
absolute path to your `python.exe`. The Desktop and terminal plugins need no shell, `.cmd` file or PowerShell step. Reopen the
terminal client and look for **Agent graph** in its command palette (`/graph`).
