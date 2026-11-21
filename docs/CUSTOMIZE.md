# Customizing the toolkit and updating it

You can make this setup your own (presets, models, agents, prompts, skills, flows) and still pull the toolkit's
improvements later. This page says what is yours, what is the toolkit's, which one wins, and how to undo an update.

## Install

```sh
git clone https://github.com/achitaan/opencode-agentgraph ~/.config/opencode/orchestration
cd ~/.config/opencode/orchestration
sh install.sh --dry-run   # lists what would be added or changed; writes nothing
sh install.sh             # copies commands, skills, presets and prompts into ~/.config/opencode
```

`OPENCODE_CONFIG_DIR` points the install at another folder (to try it out, use a scratch one). The install
needs `python3`. It writes a small manifest, `~/.config/opencode/.toolkit-install.json`: the hash of every file
it wrote and the toolkit's last `oh-my-opencode-slim.json`. That is how the next run knows what you changed.

## Update

```sh
cd ~/.config/opencode/orchestration
git pull
sh install.sh --dry-run
sh install.sh
```

For each file the toolkit ships, `install.sh` compares three things: the toolkit's new version, what it wrote
last time, and what is in your folder now.

| Your file | What happens |
|---|---|
| You never changed it | Replaced by the toolkit's new version (the old one goes to `backups/`). |
| You changed it, the toolkit did not | Kept, nothing printed. |
| You changed it and the toolkit did too | **Yours is kept.** The toolkit's version is written beside it as `<name>.toolkit-new` and listed at the end. Merge what you want, then delete the `.toolkit-new` file. Once your file equals the toolkit's, the copy beside it is removed on the next run. If a `<name>.toolkit-new` is already there with other text (you may be merging into it), it is not overwritten: it is copied to that run's backup folder first, and the install says so. |
| You made it (your own skill, your own `*_append.md`, a file inside a toolkit skill) | Never touched, never deleted. |
| You deleted a toolkit file | Not brought back, unless the toolkit changes it: then the new version arrives as `<name>.toolkit-new`. |
| A file that differs and the manifest has no entry for (the first run over an install from before the manifest) | Treated as yours: kept, with `<name>.toolkit-new` beside it. |
| A file the toolkit no longer ships | Left where it is. |

`oh-my-opencode-slim.json` is merged key by key instead: the toolkit's new agents, presets and keys arrive; the
`preset` you chose, models, skills lists and agents you added or edited stay; a key you removed stays removed.
A value you never touched follows the toolkit. If the file is not valid JSON it is left alone and the toolkit's
copy is written beside it.

`AGENTS.md` follows the same rules as every other file, so notes you added to it are kept and the toolkit's rules
arrive as `AGENTS.md.toolkit-new`.

## Backups and restore

Before the install replaces or merges a file it copies the old one to `~/.config/opencode/backups/<time>-pre-toolkit.*`.

```sh
sh install.sh --list-backups                    # the toolkit's backups, with file counts
sh install.sh --restore <name> --dry-run        # which files would come back
sh install.sh --restore <name>                  # put them back
```

A restore first copies what it replaces to a `<time>-pre-restore.*` backup, so a restore can be undone the same
way. A restored file now differs from the toolkit's current version, so the next install keeps it and writes the
toolkit's version beside it as `<name>.toolkit-new` (the restore says so). The install writes each file whole or not
at all, and the config folder itself may be a symlink (links inside it are refused). Only `-pre-toolkit` and `-pre-restore` backups can be restored; backups made by
the editors in the view have their own Undo there.

## What is personal and what is the toolkit's

| Item | Lives in | Kind |
|---|---|---|
| Commands, toolkit skills, `oh-my-opencode-slim/` prompts, `config/AGENTS.md` | the checkout, installed into `~/.config/opencode` | toolkit default; edits you make in the installed copy are yours and survive updates |
| `oh-my-opencode-slim.json` (roster, models, chosen preset) | `~/.config/opencode` | shipped by the toolkit, then yours; merged on update |
| Your own skills, `agents/NAME.md`, your own `*_append.md` | `~/.config/opencode` | yours |
| `structure/` (agent descriptions, your flows and teams), `SKILLS.md`, `task-profiles.md`, `setups/` | the checkout, or your personal folder | yours once you edit them (see below) |
| Run records: `.opencode/`, `memory*/`, `proposals*/`, `out/`, `cost-settings.jsonl` | the checkout, git-ignored | yours |

### The personal folder

`structure/`, `SKILLS.md`, `task-profiles.md` and `setups/` are written by the editors in the view, so they are
tracked in git and a `git pull` can conflict with your edits. Move them out of the checkout:

```sh
python3 -m agentgraph.paths init   # copies the checkout's copies into ~/.config/opencode/agentgraph-personal/
python3 -m agentgraph.paths show   # which copy is in use for each item, and why
```

`init` never overwrites: an item that is already in the personal folder is refused and left as it is. From the
next start of agentgraph the editors read and write the personal copies.

Which copy is used, first match wins:

| # | Rule | Where |
|---|---|---|
| 1 | `AGENTGRAPH_PERSONAL` is set to a folder that exists | that folder |
| 2 | `~/.config/opencode/agentgraph-personal/` exists | that folder |
| 3 | neither | the checkout's copies (how it worked before the personal folder existed) |

Inside the chosen folder each item is used if it is there, otherwise that item falls back to the checkout's copy
(`structure/` is taken whole: with a personal `structure/`, groups it does not contain are not drawn).
`setups/` is the exception: with a personal folder it is always the folder's own, created on first save.
If `AGENTGRAPH_PERSONAL` names a folder that does not exist it is ignored, and `show` says so.

Other precedence that already applies:

| Setting | Order, first wins |
|---|---|
| Active preset | `OH_MY_OPENCODE_SLIM_PRESET` env var, then the project's `.opencode/oh-my-opencode-slim.jsonc` or `.json`, then `preset` in `~/.config/opencode/oh-my-opencode-slim.json` |
| An agent's prompt | `oh-my-opencode-slim/<preset>/<agent>_append.md`, then `oh-my-opencode-slim/<agent>_append.md` |
| Instructions | your `~/.config/opencode/AGENTS.md`; the toolkit's is `config/AGENTS.md`; a project's own `AGENTS.md` is separate |

## Keeping the checkout in sync with your setup (for the toolkit's author)

`sh sync.sh` copies the installed commands, skills, presets and prompts back into the checkout so a change can be
reviewed with `git diff`. What is personal to you comes from `sync.conf` (git-ignored; copy
`sync.conf.example`): `SEPARATE_SKILLS`, skills to keep out of the repository, and `AGENTS_CUT_MARKER`, the line
where your own notes start in `AGENTS.md`. Without a marker `config/AGENTS.md` is not synced.
