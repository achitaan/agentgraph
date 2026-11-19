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
"plugins": ["oh-my-opencode-slim", "~/.config/opencode/orchestration/tui-plugin"]
```

`python` must be Python 3.11 or newer, given as an absolute path. Write the plugin paths out in full if your OpenCode version does not expand `~`.
