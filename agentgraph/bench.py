"""A reproducible bench for the toolkit: the same fixed tasks run before and after a change to prompts or settings,
measured in tokens, cache reads and writes, cost, time, agent calls, correctness and recovery.

    agentgraph bench scratch-config --to DIR [--pin-model M] [--json]
    agentgraph bench plain-config --to DIR [--pin-model M] [--json]
    agentgraph bench run SCENARIO... --label L --config DIR [--reps N] [--interrupt-after SECONDS] [--budget USD] [--keep-work] [--no-sandbox] [--json]
    agentgraph bench probe AGENT... --label L --config DIR [--keep-work] [--no-sandbox] [--json]
    agentgraph bench report LABEL [LABEL2] [--results DIR]
    agentgraph bench suite NAME --setup LABEL=DIR --setup LABEL=DIR [--reps N] [--budget USD] [--interrupt-after SECONDS] [--append] [--keep-work] [--no-sandbox] [--json]
    agentgraph bench card LABEL_A LABEL_B [--html FILE] [--suite NAME] [--results DIR]
    agentgraph bench scenarios
    agentgraph bench fingerprint

**Isolation.** Nothing here ever runs against the live OpenCode config. `scratch-config` copies it into DIR (mode 0700,
under the temp folder) as `DIR/home/.config/opencode`, and every run and probe sets `HOME=DIR/home` (and
`XDG_CONFIG_HOME=DIR/home/.config`). A scratch HOME, not only a config switch, because: the oh-my-opencode-slim plugin's
`OPENCODE_CONFIG_DIR` is merely an *extra* folder merged with the default one; and the prompt files say
`~/.config/opencode/...` (and `Path.home()` in the toolkit's code), which a scratch HOME resolves to the scratch copy
with the prompt text left byte-for-byte as it is, so first-turn prompt tokens match the live config. Under the scratch
HOME, `.local/share/opencode` (the database, so sessions are stored and costed in the real one), `.local/state/opencode`
and `.cache/opencode` (the plugin's package cache) are symlinks to the real ones. `OPENCODE_CONFIG_CONTENT` (the provider
config) and the proxy helpers stay in the environment as they are (except what the Sandbox paragraph drops); `OPENCODE_CONFIG_DIR`, `OPENCODE_CONFIG` and
`OPENCODE_TUI_CONFIG` are removed from it. `AGENTGRAPH_HOME` points at the scratch ledger (an empty `memory/`), so a bench
agent that runs `agentgraph record` or `check` touches only the scratch toolkit. Only the absolute path of the live
folder inside copied text files (`opencode.json`, `cli.json`: the plugin paths) is rewritten to the scratch folder.
Not copied: `.git`, `.opencode/`, `memory*/`, `proposals*/`, `out/`, `bench/`, `tests/`, `PROGRESS.md`, backups,
`service.json`, `*.bak`. The scratch folder holds a marker file; `run` and `probe` refuse a folder without it, the live
config folder, and anything inside it. The command is always the app CLI
(`/Applications/OpenCode.app/Contents/Resources/opencode-cli`, `OPENCODE_CLI` overrides, an absolute path only) with
`--standalone` (a private server; the background service is never attached to, started or stopped).

**Work folders and hidden checks.** Each run works in a brand-new `tempfile.mkdtemp` folder inside the temp folder (not under
the scratch folder). The hidden check never runs there: `run_check` moves the model's folder aside, puts a throwaway copy
(symlinks and sockets dropped) at the same path, copies `hidden/` into the copy, runs the check, deletes the copy and moves the model's
folder back (the p4 checks find the session by that path). When the row has been written the work folder is deleted, unless
`--keep-work` is given; kept folders hold no `hidden/` but must be removed before another run (a later model could read them).
`run`, `suite` and `probe` refuse a scratch folder that still holds a `work/` folder of the older layout (it holds hidden checks).

**Sandbox.** On macOS, when `/usr/bin/sandbox-exec` exists, the host runs under a profile (`--no-sandbox` turns it off; each
row has `"sandbox": true|false`) that allows everything except: reading any `bench/scenarios` folder or git directory of this
checkout, of every `git worktree list` path of it and of the live toolkit checkout; writing anywhere under the real config
folder or under those checkouts. The runner itself is not sandboxed. The real database stays readable (a known gap).
`run_env` drops variables whose value points into this repository or the real config folder (a PATH entry is dropped alone);
for a plain copy `OPENCODE_CONFIG_CONTENT` keeps only the `PLAIN_KEYS` and keys that start with `provider` (dropped if it
is not JSON).

**Plain copy.** `plain-config` makes a scratch HOME the same way (same checks, same links, same model pin) that holds plain
OpenCode and nothing of the toolkit: from `opencode.json` only the keys in `PLAIN_KEYS` (the provider entries and the
models), and no agent files, prompts, plugins, skills, `AGENTS.md`, commands or MCP servers. Both kinds of copy hold a
`bench-setup.json` (`kind` toolkit or plain, and for plain the `agent` to run, `build`, the one built-in agent): a run uses
that agent instead of the scenario's. No copy ever holds a `bench/scenarios` folder (the hidden checks).

**Suites** (`bench/suites/<name>.json`: name, note, scenarios) run every scenario on every setup, one rep of one scenario on
each setup in turn, the first setup alternating, so drift over time reaches all setups; `--budget` is one total over all of
them. Each row gets a `suite` field. `card` puts two labels side by side (`agentgraph/benchcard.py`). The setups must pin the
same model. The budget is checked only between rounds (one scenario and rep on every setup), so every setup ends with the
same number of runs. Two runs in a row with no session in the database stop the suite (exit 4: the cost cannot be tracked).
A suite whose result files already hold rows for the same label and suite refuses to start unless `--append` is given, then rep
numbers continue after the highest one present.

**Sessions** go to the real OpenCode database as archived sessions titled `agentgraph-bench: <scenario> <label> rN`
(the host has no per-run data folder switch short of moving the credentials too), so the orchestrator's spend limit
counts them; every row reports the cost, read from the database (read-only), for the root session and each descendant.

**Model pinning.** `--pin-model M` (default `anthropic/claude-sonnet-5-5`) sets every `model` of the plugin config
(presets, council), the top-level and per-agent `model` of `opencode.json` and the `model:` line of every agent file to M
in the scratch copy, and drops the `variant` that belonged to the old model, so prompt text is the only difference between
two configs. `config_hash` is a hash of the prompt files (AGENTS.md, agents, commands, skills, the plugin config and its
appends, `opencode.json`, `cli.json`, the toolkit's `*.md`) with the scratch path replaced by a placeholder, so two
scratch folders with the same prompt text have the same hash. Only hashes and counts are ever printed.

**Scenarios** (`bench/scenarios/<id>/scenario.json` + `fixture/` + `hidden/`): each rep copies the fixture to a fresh work
folder (git init, one commit), runs the agent with the prompt on stdin, copies `hidden/` over the work folder and runs the
`check` command. `s3-workflow` with `--interrupt-after N` stops the run's process tree after N seconds, then resumes the
same session with a fixed "continue from the graph checkpoint" prompt, and the row's `recovery` says what the graph file
shows: `interrupted_at`, `nodes_passed_before`, `nodes_dispatched_again` (nodes that had passed and were dispatched again),
`nodes_dispatched_after`, `resumed_ok` (the check passed and the graph is `passed`).

**probe** sends "Reply with the single word OK." to each agent and reads its first-turn prompt tokens (input + cache read +
cache write): the tool schemas and built-in prompts included, which `agentgraph bloat` cannot count. `opencode run --agent`
takes the name of any agent the host knows, including plugin sub-agents, so the probe runs the agent itself.

Row fields (`bench/results/<label>.jsonl`): scenario, label, rep, agent, time, config_hash, model, wall_seconds, root_session,
sessions, agent_calls, turns, tokens {input, output, reasoning, cache_read, cache_write}, first_turn_tokens {agent: n},
max_context, cost, cost_sessions, models_used (provider/model ids on the sessions' turns, when the database has them),
check {passed, detail}, recovery, exit_code, error, sandbox, work (only with --keep-work). `error` and `check.detail` pass
through `mask` before a row is written. The check runs with a minimal environment (PATH, HOME of the scratch copy,
PYTHONDONTWRITEBYTECODE, and P4_DB for the database path), not the real one.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import statistics
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path

from . import cost as costmod
from . import osfs, osproc, sidechat, sources

HERE = Path(__file__).resolve().parent.parent
BENCH_DIR = HERE / "bench"
SCENARIOS = BENCH_DIR / "scenarios"
RESULTS = BENCH_DIR / "results"
TEMP_ROOT = Path("/private/var/folders/pm/r8xm1mr525v913lct5tdg_5w0000gn/T/opencode")
DEFAULT_PIN = "anthropic/claude-sonnet-5-5"
MARKER = ".agentgraph-bench-scratch"
SETUP_FILE = "bench-setup.json"
PLAIN_AGENT = "build"
# What a plain copy keeps of opencode.json: the provider entries and the models. Everything else (plugins, agents, MCP
# servers, commands, instruction files, permissions) is left out.
PLAIN_KEYS = ("$schema", "provider", "enabled_providers", "disabled_providers", "model", "small_model")
SUITES = BENCH_DIR / "suites"
PROBE_PROMPT = "Reply with the single word OK."
RESUME_PROMPT = ("Continue from the graph checkpoint: read the task graph in .opencode/graph/, work only the nodes that have "
                 "not passed (do not redo a node that passed), and finish the task.")
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,47}")
MODEL = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.:-]+")
SESSION = re.compile(r"ses_[A-Za-z0-9]+")
KEYLIKE = re.compile(r"[A-Za-z0-9]{24,}")  # a key, token or session id: long runs of letters and digits
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
WORK_PREFIX = "agentgraph-bench-work-"
EXIT_BUDGET, EXIT_UNRECORDED = 3, 4
TEXT_SUFFIXES = {".md", ".json", ".jsonl", ".py", ".sh", ".txt", ".yaml", ".yml", ".toml", ".html", ".js", ".mjs", ".ts", ""}
TOP_FILES = ("AGENTS.md", "opencode.json", "cli.json", "tui.json", "package.json", "package-lock.json",
             "oh-my-opencode-slim.json")
TOP_DIRS = ("agents", "commands", "skills", "oh-my-opencode-slim", "orchestration", "node_modules")
GLOBAL_SKIP = {".git", ".opencode", "__pycache__", ".DS_Store"}
SKIP_NAMES = {"bench", "out", "setups", "tests", "PROGRESS.md", "cost-settings.jsonl"}
SKIP_PREFIXES = ("memory", "proposals")
PROMPT_PATHS = ("AGENTS.md", "opencode.json", "cli.json", "oh-my-opencode-slim.json", "agents", "commands", "skills",
                "oh-my-opencode-slim")


class BenchError(Exception):
    """A refused request; the message says why and nothing was run."""


# ---- the scratch copy of the config ----

def _skip(name: str, relative: Path) -> bool:
    if relative.parts[-2:] == ("bench", "scenarios"):  # the hidden checks, wherever a copied folder holds them
        return True
    if name in GLOBAL_SKIP or name.endswith((".bak", ".pyc")) or name.startswith((".oh-my-opencode-slim", ".lock")):
        return True
    if relative.parts[:1] == ("orchestration",) and len(relative.parts) == 2:  # the toolkit's own personal folders
        return name in SKIP_NAMES or name.startswith(SKIP_PREFIXES)
    return False


def _path_pattern(live: Path) -> re.Pattern:
    """The absolute path of the live config folder. `~/.config/opencode` is left alone: a scratch HOME resolves it."""
    return re.compile(re.escape(str(live)) + r"(?![\w-])")


def rewrite_text(text: str, live: Path, config: Path) -> str:
    """The text with the absolute path of the live config folder pointing at the scratch folder."""
    return _path_pattern(live).sub(lambda _m: str(config), text)


def _copy_tree(source: Path, target: Path, live: Path, config: Path, relative: Path = Path()) -> int:
    count = 0
    target.mkdir(parents=True, exist_ok=True)
    for entry in sorted(source.iterdir()):
        rel = relative / entry.name
        if _skip(entry.name, rel):
            continue
        if entry.is_symlink():
            continue
        if entry.is_dir():
            count += _copy_tree(entry, target / entry.name, live, config, rel)
        elif entry.is_file():
            count += _copy_file(entry, target / entry.name, live, config, rel)
    return count


def _copy_file(source: Path, target: Path, live: Path, config: Path, relative: Path) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    if "node_modules" not in relative.parts and source.suffix.lower() in TEXT_SUFFIXES and source.stat().st_size < 2_000_000:
        try:
            text = source.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            shutil.copy2(source, target)
            return 1
        target.write_text(rewrite_text(text, live, config), encoding="utf-8")
        shutil.copystat(source, target)
        return 1
    shutil.copy2(source, target)
    return 1


def _pin_models(node, model: str):
    """Every `model` string in a plugin config set to `model`; the `variant` next to it dropped."""
    if isinstance(node, dict):
        pinned = {key: _pin_models(value, model) for key, value in node.items()}
        if isinstance(pinned.get("model"), str):
            pinned["model"] = model
            pinned.pop("variant", None)
        return pinned
    if isinstance(node, list):
        return [_pin_models(value, model) for value in node]
    return node


def _pin_opencode_json(config: dict, model: str) -> dict:
    for key in ("model", "small_model"):
        if isinstance(config.get(key), str):
            config[key] = model
    agents = config.get("agent")
    if isinstance(agents, dict):
        for entry in agents.values():
            if isinstance(entry, dict) and isinstance(entry.get("model"), str):
                entry["model"] = model
                entry.pop("variant", None)
    return config


def _pin_agent_file(text: str, model: str) -> str:
    head = re.match(r"---\r?\n.*?\r?\n---", text, re.S)
    if not head:
        return text
    block = re.sub(r"^model:.*$", f'model: "{model}"', head.group(0), flags=re.M)
    block = re.sub(r"^variant:.*\n", "", block, flags=re.M)
    return block + text[head.end():]


def _pin(config: Path, model: str) -> int:
    changed = 0
    for name, fix in (("oh-my-opencode-slim.json", lambda d: _pin_models(d, model)),
                      ("opencode.json", lambda d: _pin_opencode_json(d, model))):
        path = config / name
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            path.write_text(json.dumps(fix(data), indent=2) + "\n", encoding="utf-8")
            changed += 1
    for path in sorted((config / "agents").glob("*.md")) if (config / "agents").is_dir() else []:
        old = path.read_text(encoding="utf-8")
        new = _pin_agent_file(old, model)
        if new != old:
            path.write_text(new, encoding="utf-8")
            changed += 1
    return changed


def _prompt_files(config: Path) -> list[Path]:
    found = []
    for name in PROMPT_PATHS:
        path = config / name
        if path.is_file():
            found.append(path)
        elif path.is_dir():
            found += [p for p in path.rglob("*") if p.is_file() and "node_modules" not in p.parts]
    found += [p for p in (config / "orchestration").glob("*.md")] if (config / "orchestration").is_dir() else []
    return sorted(set(found))


def config_hash(config: Path, scratch: Path | None = None) -> tuple[str, int]:
    """(hash of the prompt files, their count): the scratch path inside them is replaced by a placeholder."""
    digest, files = hashlib.sha256(), _prompt_files(config)
    for path in files:
        text = path.read_bytes().decode("utf-8", "replace")
        text = text.replace(str(config), "@CONFIG@")
        if scratch is not None:
            text = text.replace(str(scratch), "@SCRATCH@")
        digest.update(str(path.relative_to(config)).encode() + b"\0" + text.encode() + b"\0")
    return digest.hexdigest()[:16], len(files)


def live_fingerprint(live: Path | None = None) -> dict:
    """Hashes of what a bench must never change: the live config's own files and the real ledgers (`memory*/`). The
    toolkit's code is left out, other work edits it. Prints nothing but hashes and counts."""
    live = Path(live) if live is not None else sources.CONFIG_DIR
    groups = {"config": [live / n for n in TOP_FILES if n != "package.json"] + [live / "agents", live / "commands",
              live / "skills", live / "oh-my-opencode-slim"], "ledger": sorted((live / "orchestration").glob("memory*"))}
    result = {}
    for name, paths in groups.items():
        digest, count = hashlib.sha256(), 0
        for path in paths:
            files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else []
            for item in files:
                digest.update(str(item.relative_to(live)).encode() + b"\0" + item.read_bytes() + b"\0")
                count += 1
        result[name] = {"hash": digest.hexdigest()[:16], "files": count}
    return result


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except ValueError:
        return False


def _make_junction(link: Path, real: Path) -> None:
    """Windows only: a directory junction, which needs no privilege (a symlink does)."""
    import _winapi
    _winapi.CreateJunction(str(real), str(link))


def link_folder(link: Path, real: Path, *, windows=None, junction=None) -> None:
    """Make `link` a link to the folder `real`. Everywhere but Windows (and wherever symlinks work): a symlink, as ever. On Windows a
    user without the symlink privilege gets a directory junction instead; when that fails too the scratch home cannot share the real
    database, state and cache folders, and a copy would read and write the wrong ones, so it is refused with the reason."""
    try:
        link.symlink_to(real)
        return
    except (OSError, NotImplementedError) as exc:
        if not (osproc.is_windows() if windows is None else windows):
            raise
        first = exc
    try:
        (junction or _make_junction)(link, real)
    except (OSError, ImportError, AttributeError) as exc:
        raise BenchError(f"cannot link {link} to {real}: no symlink ({first}) and no junction ({exc}); turn on Windows Developer Mode "
                         f"(it allows symlinks without administrator rights)") from exc


def _new_scratch(to, *, pin_model, live: Path, temp_root: Path | None, real_home: Path | None) -> tuple[Path, Path, Path]:
    """The checks both kinds of copy share, then the empty scratch folder with its links to the real database, state and
    cache folders: (dir, home, config)."""
    real_home = Path(real_home) if real_home is not None else Path.home()
    root = Path(temp_root) if temp_root is not None else TEMP_ROOT
    target = Path(to).expanduser()
    if not target.is_absolute():
        raise BenchError("--to must be an absolute path")
    target = Path(os.path.abspath(target))
    if _inside(target, live) or _inside(live, target):
        raise BenchError("the scratch folder must be outside the live config folder")
    if not _inside(target, root) or target.resolve() == root.resolve():
        raise BenchError(f"the scratch folder must be a new folder inside {root}")
    if pin_model is not None and not MODEL.fullmatch(pin_model):
        raise BenchError("--pin-model must look like provider/model")
    if target.exists() and any(target.iterdir()):
        raise BenchError(f"{target} exists and is not empty; use a new folder")
    if not live.is_dir():
        raise BenchError(f"no live config folder at {live}")
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    osfs.chmod(target, 0o700)
    target = target.resolve()
    home = target / "home"
    config = home / ".config" / "opencode"
    config.mkdir(parents=True, mode=0o700)
    for link, real in ((home / ".local" / "share" / "opencode", sources.DB_PATH.parent),
                       (home / ".local" / "state" / "opencode", real_home / ".local" / "state" / "opencode"),
                       (home / ".cache" / "opencode", real_home / ".cache" / "opencode")):
        if real.is_dir():
            link.parent.mkdir(parents=True, exist_ok=True)
            link_folder(link, real)
    return target, home, config


def _write_setup(target: Path, kind: str, agent: str | None) -> None:
    (target / SETUP_FILE).write_text(json.dumps({"kind": kind, "agent": agent}) + "\n", encoding="utf-8")


def scratch_config(to, *, pin_model: str | None = DEFAULT_PIN, live: Path | None = None,
                   temp_root: Path | None = None, real_home: Path | None = None) -> dict:
    """Copy the live config into the scratch folder `to` (mode 0700) and return {dir, config, prompt_hash, files, pinned}."""
    live = Path(live) if live is not None else sources.CONFIG_DIR
    target, home, config = _new_scratch(to, pin_model=pin_model, live=live, temp_root=temp_root, real_home=real_home)
    copied = 0
    for name in TOP_FILES:
        if (live / name).is_file():
            copied += _copy_file(live / name, config / name, live, config, Path(name))
    for name in TOP_DIRS:
        if (live / name).is_dir():
            copied += _copy_tree(live / name, config / name, live, config, Path(name))
    (config / "orchestration" / "memory").mkdir(parents=True, exist_ok=True)  # an empty scratch ledger
    pinned = _pin(config, pin_model) if pin_model else 0
    digest, files = config_hash(config, target)
    (target / MARKER).write_text(json.dumps({"created": int(time.time()), "prompt_hash": digest, "pin_model": pin_model,
                                             "files": copied}) + "\n", encoding="utf-8")
    _write_setup(target, "toolkit", None)
    return {"dir": str(target), "home": str(home), "config": str(config), "prompt_hash": digest, "prompt_files": files, "files_copied": copied,
            "pinned": pinned, "pin_model": pin_model, "kind": "toolkit"}


def file_reference_keys(node, live: Path, real_home: Path, trail: str = "") -> list[str]:
    """The keys (a dotted path, never a value) of every string that refers to a file: a `{file:` reference or a path in the
    config folder, written absolute or with `~`."""
    config = [str(live), str(Path(live).resolve()), str(real_home / ".config" / "opencode"), "~/.config/opencode",
              "$HOME/.config/opencode"]
    if isinstance(node, str):
        return [trail] if "{file:" in node or any(path in node for path in config) else []
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            found += file_reference_keys(value, live, real_home, f"{trail}.{key}" if trail else str(key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found += file_reference_keys(value, live, real_home, f"{trail}[{index}]")
    return found


def plain_config(to, *, pin_model: str | None = DEFAULT_PIN, live: Path | None = None,
                 temp_root: Path | None = None, real_home: Path | None = None) -> dict:
    """A scratch folder of plain OpenCode: only the `PLAIN_KEYS` of the live `opencode.json` (the model pinned), nothing else
    of the config. Returns the same fields as `scratch_config`."""
    live = Path(live) if live is not None else sources.CONFIG_DIR
    real_home = Path(real_home) if real_home is not None else Path.home()
    kept, copied, source = {}, 0, live / "opencode.json"
    if source.is_file():
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise BenchError("the live opencode.json could not be read as JSON") from None
        kept = {key: data[key] for key in PLAIN_KEYS if isinstance(data, dict) and key in data}
        refs = file_reference_keys(kept, live, real_home)
        if refs:
            raise BenchError("a plain copy cannot hold settings that refer to a file; remove these keys from the live "
                             "opencode.json settings or set them in the environment: " + ", ".join(refs[:8]))
    target, home, config = _new_scratch(to, pin_model=pin_model, live=live, temp_root=temp_root, real_home=real_home)
    kept = json.loads(rewrite_text(json.dumps(kept), live, config))
    if pin_model:
        kept = _pin_opencode_json(kept, pin_model)
        kept["model"] = pin_model
    if kept:
        (config / "opencode.json").write_text(json.dumps(kept, indent=2) + "\n", encoding="utf-8")
        copied = 1
    digest, files = config_hash(config, target)
    (target / MARKER).write_text(json.dumps({"created": int(time.time()), "prompt_hash": digest, "pin_model": pin_model,
                                             "files": copied}) + "\n", encoding="utf-8")
    _write_setup(target, "plain", PLAIN_AGENT)
    return {"dir": str(target), "home": str(home), "config": str(config), "prompt_hash": digest, "prompt_files": files,
            "files_copied": copied, "pinned": 1 if pin_model else 0, "pin_model": pin_model, "kind": "plain",
            "keys": sorted(kept)}


def open_scratch(config, *, live: Path | None = None) -> dict:
    """Validate a scratch folder given to run/probe: {dir, config, hash, model}. Refuses anything else."""
    if config is None:
        raise BenchError("--config DIR is required: the bench never runs on the live config")
    live = Path(live) if live is not None else sources.CONFIG_DIR
    path = Path(config).expanduser()
    if not path.is_absolute():
        raise BenchError("--config must be an absolute path")
    path = path.resolve()
    if path.name == "opencode" and path.parent.name == ".config" and (path.parents[2] / MARKER).is_file():
        path = path.parents[2]
    if path == live.resolve() or _inside(path, live) or _inside(live, path):
        raise BenchError("--config is the live config folder; the bench runs only on a scratch copy")
    marker = path / MARKER
    if not marker.is_file() or not (path / "home" / ".config" / "opencode").is_dir():
        raise BenchError(f"{path} is not a scratch config made by `agentgraph bench scratch-config`")
    try:
        info = json.loads(marker.read_text(encoding="utf-8"))
    except ValueError:
        raise BenchError(f"{marker} is unreadable") from None
    cfg = path / "home" / ".config" / "opencode"
    digest, _ = config_hash(cfg, path)
    try:
        setup = json.loads((path / SETUP_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        setup = {}  # a copy made before the setup file existed is a toolkit copy
    setup = setup if isinstance(setup, dict) else {}
    agent = setup.get("agent") if isinstance(setup.get("agent"), str) else None
    return {"dir": path, "config": cfg, "hash": digest, "pin_model": info.get("pin_model"), "made": info.get("prompt_hash"),
            "kind": setup.get("kind") if setup.get("kind") in ("toolkit", "plain") else "toolkit", "agent": agent}


def _drop_paths(live: Path | None) -> list[str]:
    """The folders no environment variable of a run may point into: this repository and the real config folder."""
    live = Path(live) if live is not None else sources.CONFIG_DIR
    return sorted({str(p) for base in (HERE, live) for p in (base, base.resolve())})


def run_env(scratch: dict, work: Path, environ=None, live: Path | None = None) -> dict:
    env = dict(os.environ if environ is None else environ)
    for name in ("OPENCODE_CONFIG_DIR", "OPENCODE_CONFIG", "OPENCODE_TUI_CONFIG"):
        env.pop(name, None)  # an extra folder or file would be merged into the scratch config
    folders = _drop_paths(live)
    for name, value in list(env.items()):
        if name == "OPENCODE_CONFIG_CONTENT":
            continue
        if name == "PATH":  # only the entries that point into those folders go
            env[name] = os.pathsep.join(e for e in value.split(os.pathsep) if not any(f in e for f in folders))
        elif any(f in value for f in folders):
            del env[name]  # OLDPWD, PWD and the like
    if scratch.get("kind") == "plain" and "OPENCODE_CONFIG_CONTENT" in env:
        try:
            content = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        except ValueError:
            content = None
        if isinstance(content, dict):
            # the app's own provider table has a longer key name; it is provider access, not toolkit
            env["OPENCODE_CONFIG_CONTENT"] = json.dumps({k: v for k, v in content.items() if k in PLAIN_KEYS or k.startswith("provider")})
        else:
            del env["OPENCODE_CONFIG_CONTENT"]
    env["HOME"] = str(scratch["dir"] / "home")
    env["XDG_CONFIG_HOME"] = str(scratch["dir"] / "home" / ".config")
    env["AGENTGRAPH_HOME"] = str(scratch["config"] / "orchestration" / "memory")
    env["PWD"] = str(work)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if osproc.is_windows():  # Windows programs read the profile folder from USERPROFILE, not HOME
        env["USERPROFILE"] = env["HOME"]
    return env


# ---- the sandbox ----

def protected_paths(live: Path | None = None) -> tuple[list[Path], list[Path]]:
    """(folders the host may not read, folders it may not write): the `bench/scenarios` folder and the git directory of this
    checkout, of every worktree `git worktree list` names for it and of the live toolkit checkout; writes are refused under
    those checkouts and under the real config folder."""
    live = Path(live) if live is not None else sources.CONFIG_DIR
    checkouts = [HERE]
    try:
        out = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=HERE, capture_output=True, text=True, timeout=30,
                             **osproc.text_kwargs()).stdout
        checkouts += [Path(line[9:]) for line in out.splitlines() if line.startswith("worktree ")]
    except (OSError, subprocess.SubprocessError):
        pass
    checkouts.append(live / "orchestration")
    reads, writes = [], [live]
    for checkout in checkouts:
        reads.append(checkout / "bench" / "scenarios")
        writes.append(checkout)
    try:
        common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=HERE, capture_output=True,
                                text=True, timeout=30, **osproc.text_kwargs()).stdout.strip()
        if common:
            reads.append(Path(common))
    except (OSError, subprocess.SubprocessError):
        pass
    return reads, writes


def _quoted(path) -> str:
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def sandbox_profile(deny_read, deny_write) -> str:
    """A sandbox-exec profile that allows everything except reading the first folders and writing under the second ones
    (each in its written and its resolved form: the profile matches real paths)."""
    lines = ["(version 1)", "(allow default)"]
    for action, folders in (("file-read*", deny_read), ("file-write*", deny_write)):
        for folder in sorted({str(p) for f in folders for p in (Path(f), Path(os.path.realpath(f)))}):
            lines.append(f"(deny {action} (subpath {_quoted(folder)}))")
    return "\n".join(lines) + "\n"


def sandbox_available() -> bool:
    return sys.platform == "darwin" and os.path.isfile(SANDBOX_EXEC)


def sandbox_note(enabled: bool = True, windows=None) -> str | None:
    """On Windows the sandbox (a macOS `sandbox-exec` profile) cannot exist: the host runs without it, as on any system that lacks the
    sandbox, and the rows say `"sandbox": false`. This is the line to tell the user so; None everywhere else."""
    if enabled and (osproc.is_windows() if windows is None else windows):
        return "bench: the sandbox is macOS-only; on Windows the host runs without it (rows say sandbox: false). Use a throwaway checkout and a scratch config."
    return None


def sandboxed(cmd: list[str], enabled: bool, live: Path | None = None) -> list[str]:
    """`cmd` under the profile when the sandbox is wanted and exists, else unchanged."""
    if not enabled or not sandbox_available():
        return cmd
    reads, writes = protected_paths(live)
    return [SANDBOX_EXEC, "-p", sandbox_profile(reads, writes), *cmd]


def mask(text, home: Path | None = None) -> str:
    """Text for a public row: the home path becomes `~`, anything that looks like a key or token (24 or more letters and
    digits in a row) becomes `[hidden]`, at most 160 characters."""
    text = str(text or "")
    home = Path(home) if home is not None else Path.home()
    for folder in sorted({str(home), str(home.resolve())}, key=len, reverse=True):
        if len(folder) > 1:
            text = text.replace(folder, "~")
    return KEYLIKE.sub("[hidden]", text)[:160]


def _mask_row(row: dict) -> dict:
    row["error"] = mask(row.get("error"))
    if isinstance(row.get("check"), dict) and "detail" in row["check"]:
        row["check"] = {**row["check"], "detail": mask(row["check"]["detail"])}
    return row


# ---- scenarios ----

def load_scenario(name: str, root: Path | None = None) -> dict:
    root = Path(root) if root is not None else SCENARIOS
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name or ""):
        raise BenchError(f"bad scenario name: {name!r}")
    base = root / name
    try:
        data = json.loads((base / "scenario.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise BenchError(f"no scenario {name!r} in {root}") from None
    if not isinstance(data, dict) or not isinstance(data.get("prompt"), str) or not isinstance(data.get("check"), str):
        raise BenchError(f"scenario {name!r} needs a prompt and a check command")
    data.setdefault("agent", "orchestrator")
    data.setdefault("timeout", 900)
    data.setdefault("check_timeout", 120)
    data["id"], data["dir"] = name, base
    return data


def list_scenarios(root: Path | None = None) -> list[str]:
    root = Path(root) if root is not None else SCENARIOS
    return sorted(p.name for p in root.iterdir() if (p / "scenario.json").is_file()) if root.is_dir() else []


def _git(work: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "user.name=bench", "-c", "user.email=bench@localhost", "-c", "commit.gpgsign=false", *args],
                   cwd=work, check=True, capture_output=True, timeout=60)


def new_work_dir() -> Path:
    """A brand-new folder of its own in the temp folder, for one run (mode 0700); never under a scratch folder."""
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=WORK_PREFIX, dir=TEMP_ROOT)).resolve()


def prepare_work(scenario_dir: Path | None, work: Path) -> None:
    work.mkdir(parents=True, exist_ok=True)
    fixture = Path(scenario_dir) / "fixture" if scenario_dir else None
    if fixture is not None and fixture.is_dir():
        shutil.copytree(fixture, work, dirs_exist_ok=True)
    else:
        (work / ".gitkeep").touch()
    _git(work, "init", "-q")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "fixture")


def _plain_entries_only(folder: str, names: list[str]) -> list[str]:
    """What the copy leaves out: symlinks, sockets and the like (only files and folders are copied)."""
    skip = []
    for name in names:
        mode = os.lstat(os.path.join(folder, name)).st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            skip.append(name)
    return skip


def run_check(scenario: dict, work: Path, env: dict, db_path: Path | None = None) -> dict:
    """Run the scenario's check on a throwaway copy of `work` with `hidden/` copied into it; `work` itself never holds
    `hidden/` and comes back as it was. The copy sits at the same path (the p4 checks find the run's sessions by the folder
    they run in): the model's folder is moved aside for the length of the check, symlinks are not copied (a link must not let
    a hidden file land elsewhere). The check gets a minimal environment, not the real one: PATH, HOME (the scratch home),
    PYTHONDONTWRITEBYTECODE and, for the p4 checks that read the OpenCode database (read-only), `P4_DB`."""
    work = Path(work)
    check_env = {"PYTHONDONTWRITEBYTECODE": "1", **{k: env[k] for k in ("PATH", "HOME", *osproc.base_env_names()) if k in env},
                 **({"P4_DB": str(db_path)} if db_path is not None else {})}
    hidden = scenario["dir"] / "hidden"
    aside = Path(tempfile.mkdtemp(prefix=WORK_PREFIX + "aside-", dir=work.parent))
    saved = aside / "model"
    osfs.rename(work, saved)
    try:
        shutil.copytree(saved, work, ignore=_plain_entries_only)
        if hidden.is_dir():
            shutil.copytree(hidden, work, dirs_exist_ok=True)
        try:
            try:
                check_args, check_shell = osproc.shell_arguments(scenario["check"])  # POSIX: the string and shell=True, as ever
            except osproc.ShellRefused as exc:  # Windows without an sh and a check of several lines: failed, with the reason
                return {"passed": False, "detail": str(exc)[:160]}
            done = subprocess.run(check_args, shell=check_shell, cwd=work, capture_output=True, text=True, errors="replace",
                                  timeout=scenario["check_timeout"], env=check_env, **osproc.text_kwargs())
        except subprocess.TimeoutExpired:
            return {"passed": False, "detail": "check timed out"}
    finally:
        shutil.rmtree(work, ignore_errors=True)
        osfs.rename(saved, work)
        shutil.rmtree(aside, ignore_errors=True)
    lines = [ln.strip() for ln in (done.stdout + "\n" + done.stderr).splitlines() if ln.strip()]
    return {"passed": done.returncode == 0, "detail": (lines[-1] if lines else f"exit {done.returncode}")[:160]}


# ---- running the host ----

def executable_path(executable: str | None = None) -> str:
    exe = executable or os.environ.get("OPENCODE_CLI")
    if not exe and osproc.is_windows():  # the usual install places only: the policy below is that PATH is never searched
        exe = sidechat.windows_binary(which=lambda _name: None)
    exe = exe or sidechat.DEFAULT_BINARY
    if not os.path.isabs(exe) or not os.path.isfile(exe):
        raise BenchError("the opencode CLI must be an absolute path to a file (set OPENCODE_CLI"
                         + ("; on Windows the full path of opencode.exe or opencode.cmd" if osproc.is_windows() else "")
                         + "); it is never looked up on PATH")
    return exe


def build_command(exe: str, *, agent: str, model: str | None, title: str | None, session: str | None = None,
                  auto: bool = True) -> list[str]:
    """Always the standalone route: a private server, never the background service."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", agent or ""):
        raise BenchError(f"bad agent name: {agent!r}")
    cmd = [exe, "run", "--standalone", "--format", "json", "--agent", agent]
    if model:
        if not MODEL.fullmatch(model):
            raise BenchError("the model must be provider/model")
        cmd += ["--model", model]
    if session:
        if not SESSION.fullmatch(session):
            raise BenchError("bad session id")
        cmd += ["--session", session]
    else:
        cmd += ["--archived", "--title", title or "agentgraph-bench"]
    if auto:
        cmd.append("--auto")
    return cmd


def _spawn(cmd: list[str], message: str, cwd: Path, env: dict, timeout: float, stop_after: float | None = None) -> dict:
    """Run one host process; stop its tree on the timeout or at `stop_after` seconds. Returns {code, seconds, stdout,
    stderr, timed_out, interrupted}."""
    started = time.time()
    proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            errors="replace", **osproc.group_kwargs(), **osproc.text_kwargs(), env=env)
    limit = timeout if stop_after is None else min(timeout, stop_after)
    timed_out = interrupted = False
    try:
        out, err = proc.communicate(input=message, timeout=limit)
    except subprocess.TimeoutExpired:
        sidechat._stop(proc)
        out, err = proc.communicate()
        interrupted = stop_after is not None and stop_after < timeout
        timed_out = not interrupted
    return {"code": None if (timed_out or interrupted) else proc.returncode, "seconds": time.time() - started,
            "stdout": out or "", "stderr": err or "", "timed_out": timed_out, "interrupted": interrupted}


# ---- reading the database ----

def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def read_tree(db_path: Path, root: str) -> dict:
    """Totals for the root session and every descendant, read-only: sessions, agent_calls, turns, tokens, first-turn prompt
    tokens per agent, max context, cost, and models_used (the sorted provider/model ids the assistant turns carry)."""
    models: set[str] = set()
    out = {"sessions": 0, "agent_calls": {}, "turns": 0, "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0,
           "cache_write": 0}, "first_turn_tokens": {}, "max_context": 0, "cost": 0.0, "cost_sessions": 0.0, "root_agent": None}
    with closing(costmod._connect(db_path)) as db:
        ids, queue = [], [root]
        while queue:
            current = queue.pop(0)
            if current in ids:
                continue
            ids.append(current)
            queue += [r[0] for r in db.execute("SELECT id FROM session_v2 WHERE parent_id=?", (current,))]
        for sid in ids:
            row = db.execute("SELECT agent, cost FROM session_v2 WHERE id=?", (sid,)).fetchone()
            if row is None:
                continue
            agent, session_cost = row[0] or "unknown", _num(row[1])
            out["sessions"] += 1
            out["cost_sessions"] += session_cost
            if sid == root:
                out["root_agent"] = agent
            else:
                users = db.execute("SELECT count(*) FROM session_message WHERE session_id=? AND type='user'", (sid,)).fetchone()[0]
                out["agent_calls"][agent] = out["agent_calls"].get(agent, 0) + max(1, int(users))
            ok = "(CASE WHEN json_valid(data) THEN json_type(data) = 'object' ELSE 0 END)"
            fields = ("cost", "tokens.input", "tokens.output", "tokens.reasoning", "tokens.cache.read", "tokens.cache.write",
                      "model.providerID", "model.id")
            cols = ", ".join(f"CASE WHEN {ok} THEN json_extract(data, '$.{f}') END" for f in fields)
            first = True
            for values in db.execute(f"SELECT {cols} FROM session_message WHERE session_id=? AND type='assistant' ORDER BY seq", (sid,)):
                c, i, o, r, cr, cw = (_num(v) for v in values[:6])
                if isinstance(values[7], str) and values[7]:
                    models.add(f"{values[6]}/{values[7]}" if isinstance(values[6], str) and values[6] else values[7])
                if values[1] is None and values[4] is None:
                    continue  # a turn still without usage numbers
                out["turns"] += 1
                out["cost"] += c
                for key, value in (("input", i), ("output", o), ("reasoning", r), ("cache_read", cr), ("cache_write", cw)):
                    out["tokens"][key] += int(value)
                context = int(i + cr + cw)
                out["max_context"] = max(out["max_context"], context)
                if first:
                    out["first_turn_tokens"].setdefault(agent, context)
                    first = False
    if models:  # left out when the turns carry no model id
        out["models_used"] = sorted(models)
    return out


def find_root(db_path: Path, title: str) -> str | None:
    try:
        with closing(costmod._connect(db_path)) as db:
            row = db.execute("SELECT id FROM session_v2 WHERE parent_id IS NULL AND title=? ORDER BY time_created DESC LIMIT 1",
                             (title,)).fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def tree_for(db_path: Path, root: str | None, title: str) -> tuple[str | None, dict | None]:
    for attempt in range(6):
        root = root or find_root(db_path, title)
        if root:
            try:
                tree = read_tree(db_path, root)
            except sqlite3.Error:
                tree = None
            if tree and tree["sessions"]:
                return root, tree
        time.sleep(0.5 if attempt < 5 else 0)
    return root, None


# ---- recovery ----

def graph_state(work: Path) -> dict:
    """What the work folder's graph files say: {passed: [...], nodes: n, events: n, dispatched: {node: count}, status}."""
    state = {"passed": [], "nodes": 0, "events": 0, "dispatched": {}, "status": None, "graphs": 0}
    folder = Path(work) / ".opencode" / "graph"
    if not folder.is_dir():
        return state
    for path in sorted(folder.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        state["graphs"] += 1
        state["status"] = data.get("status")
        nodes = [n for n in data.get("nodes", []) if isinstance(n, dict)]
        state["nodes"] += len(nodes)
        state["passed"] += [f"{path.stem}:{n.get('id')}" for n in nodes if n.get("status") == "passed"]
        events = [e for e in data.get("events", []) if isinstance(e, dict)]
        state["events"] += len(events)
        for event in events:
            if event.get("event") == "dispatched":
                key = f"{path.stem}:{event.get('node')}"
                state["dispatched"][key] = state["dispatched"].get(key, 0) + 1
    return state


def recovery_row(before: dict, after: dict, interrupted_at: float, resumed_ok: bool) -> dict:
    """Recovery accounting from the graph state at the interruption and at the end. A node that had passed and got
    dispatched again after the interruption is work paid for twice."""
    again = [n for n in before["passed"] if after["dispatched"].get(n, 0) > before["dispatched"].get(n, 0)]
    new = sum(max(0, after["dispatched"].get(n, 0) - before["dispatched"].get(n, 0)) for n in after["dispatched"])
    return {"interrupted_at": round(interrupted_at, 1), "nodes_passed_before": len(before["passed"]), "nodes_total": after["nodes"],
            "nodes_dispatched_again": len(again), "nodes_dispatched_after": new, "resumed_ok": bool(resumed_ok)}


# ---- one rep ----

def _first_line(text: str) -> str:
    return next((ln.strip() for ln in text.splitlines() if ln.strip()), "")[:160]


def one_rep(*, scenario: dict | None, agent: str, prompt: str, label: str, rep: int, scratch: dict, exe: str, db_path: Path,
            model: str | None, name: str, interrupt_after: float | None = None, timeout: float = 900, auto: bool = True,
            sandbox: bool = True, live: Path | None = None) -> dict:
    """One run in a work folder of its own. The row holds `_work` (the model's folder, no `hidden/`): `save_row` writes the
    row, then deletes the folder or keeps it."""
    scenario_id = scenario["id"] if scenario else name
    title = f"agentgraph-bench: {scenario_id} {label} r{rep}"
    work = new_work_dir()
    try:
        prepare_work(scenario["dir"] if scenario else None, work)
        env = run_env(scratch, work, live=live)
        use_sandbox = bool(sandbox and sandbox_available())
        row = {"scenario": scenario_id, "label": label, "rep": rep, "agent": agent, "time": int(time.time()),
               "config_hash": scratch["hash"], "model": model or scratch.get("pin_model"), "_work": str(work),
               "setup": scratch.get("kind", "toolkit"), "sandbox": use_sandbox}
        cmd = sandboxed(build_command(exe, agent=agent, model=model, title=title, auto=auto), use_sandbox, live)
        first = _spawn(cmd, prompt, work, env, timeout, stop_after=interrupt_after)
        events = sidechat._events(first["stdout"])
        root, wall, recovery, last = events["session"], first["seconds"], None, first
        error = events["error"] or ("timed out" if first["timed_out"] else "") or (_first_line(first["stderr"]) if first["code"] else "")
        if first["interrupted"]:
            before = graph_state(work)
            at = first["seconds"]
            root, _tree = tree_for(db_path, root, title)
            if not root:
                error = error or "no session was created before the interruption"
            else:
                resume = sandboxed(build_command(exe, agent=agent, model=model, title=None, session=root, auto=auto), use_sandbox, live)
                last = _spawn(resume, RESUME_PROMPT, work, env, timeout)
                wall += last["seconds"]
                ev2 = sidechat._events(last["stdout"])
                error = ev2["error"] or ("timed out" if last["timed_out"] else "") or (_first_line(last["stderr"]) if last["code"] else "")
                recovery = {"_before": before, "_at": at}
        check = run_check(scenario, work, env, db_path) if scenario else {"passed": None, "detail": "no check"}
        if recovery is not None:
            after = graph_state(work)
            recovery = recovery_row(recovery["_before"], after, recovery["_at"],
                                    check["passed"] is True and after["status"] == "passed")
        root, tree = tree_for(db_path, root, title)
        row.update({"wall_seconds": round(wall, 1), "root_session": root, "exit_code": last["code"], "error": error,
                    "check": check, "recovery": recovery})
        if tree is None:
            row.update({"sessions": 0, "agent_calls": {}, "turns": 0, "tokens": {}, "first_turn_tokens": {}, "max_context": 0,
                        "cost": 0.0, "cost_sessions": 0.0})
            row["error"] = row["error"] or "the session was not found in the database"
        else:
            tree.pop("root_agent", None)
            row.update(tree)
            row["cost"], row["cost_sessions"] = round(tree["cost"], 6), round(tree["cost_sessions"], 6)
        return _mask_row(row)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise


def save_row(row: dict, results: Path | None = None, keep_work: bool = False) -> Path:
    """Write the row, then delete the run's work folder (the model's; the hidden check never entered it) unless it is kept,
    when the row says where it is."""
    work = row.pop("_work", None)
    if work and keep_work:
        row["work"] = work
    try:
        return append_row(row, results)
    finally:
        if work and not keep_work:
            shutil.rmtree(work, ignore_errors=True)


def append_row(row: dict, results: Path | None = None) -> Path:
    label = _label(row.get("label"))
    results = Path(results) if results is not None else RESULTS
    results.mkdir(parents=True, exist_ok=True)
    path = results / f"{label}.jsonl"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(_mask_row(dict(row)), sort_keys=True) + "\n")
    return path


def _label(label) -> str:
    if not isinstance(label, str) or not LABEL.fullmatch(label):
        raise BenchError("--label must be letters, digits, '.', '_' or '-' (at most 48)")
    return label


def refuse_old_layout(scratch: dict) -> None:
    """The first layout kept every run's work folder under the scratch folder's `work/`, hidden checks included."""
    old = Path(scratch["dir"]) / "work"
    if old.exists() and (not old.is_dir() or any(old.iterdir())):
        raise BenchError(f"{old} holds work folders from the older layout, with hidden checks inside; delete that folder "
                         f"(rm -r '{old}') and start again")


def run_scenarios(names, *, label, config, reps=1, interrupt_after=None, budget=None, executable=None, db_path=None,
                  results=None, scenarios_root=None, live=None, out=None, sandbox=True, keep_work=False) -> list[dict]:
    label = _label(label)
    if not names:
        raise BenchError("name at least one scenario")
    scratch = open_scratch(config, live=live)
    refuse_old_layout(scratch)
    exe = executable_path(executable)
    db_path = Path(db_path) if db_path is not None else sources.DB_PATH
    scenarios = [load_scenario(n, scenarios_root) for n in names]
    if reps < 1 or reps > 20:
        raise BenchError("--reps must be 1 to 20")
    if interrupt_after is not None and interrupt_after <= 0:
        raise BenchError("--interrupt-after must be positive")
    rows, spent = [], 0.0
    for scenario in scenarios:
        for rep in range(1, reps + 1):
            if budget is not None and spent >= budget:
                raise BenchError(f"stopped before {scenario['id']} r{rep}: spent ${spent:.2f} of the ${budget:.2f} budget")
            row = _run_one(scenario, rep, label=label, scratch=scratch, exe=exe, db_path=db_path, interrupt_after=interrupt_after,
                           results=results, sandbox=sandbox, keep_work=keep_work, live=live)
            rows.append(row)
            spent += row.get("cost") or 0.0
            if out:
                print(summary_line(row), file=out, flush=True)
    return rows


def _run_one(scenario: dict, rep: int, *, label: str, scratch: dict, exe: str, db_path: Path, interrupt_after, results,
             extra: dict | None = None, sandbox: bool = True, keep_work: bool = False, live: Path | None = None) -> dict:
    """One rep of one scenario on one scratch copy, its row saved. A copy that names an agent (the plain one) runs that
    agent in place of the scenario's."""
    row = one_rep(scenario=scenario, agent=scratch.get("agent") or scenario["agent"], prompt=scenario["prompt"], label=label, rep=rep,
                  scratch=scratch, exe=exe, db_path=db_path, model=scenario.get("model") or scratch["pin_model"],
                  name=scenario["id"], interrupt_after=interrupt_after, timeout=float(scenario["timeout"]), sandbox=sandbox, live=live)
    row.update(extra or {})
    save_row(row, results, keep_work)
    return row


# ---- suites ----

def load_suite(name: str, root: Path | None = None) -> dict:
    root = Path(root) if root is not None else SUITES
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name or ""):
        raise BenchError(f"bad suite name: {name!r}")
    try:
        data = json.loads((root / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise BenchError(f"no suite {name!r} in {root}") from None
    ids = data.get("scenarios") if isinstance(data, dict) else None
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
        raise BenchError(f"suite {name!r} needs a list of scenario ids")
    return {"name": name, "note": str(data.get("note") or ""), "scenarios": ids}


def parse_setups(items) -> list[tuple[str, str]]:
    """`LABEL=DIR` strings as (label, dir)."""
    setups = []
    for item in items or []:
        label, sep, folder = str(item).partition("=")
        if not sep or not folder:
            raise BenchError(f"--setup must look like LABEL=DIR, not {item!r}")
        setups.append((label, folder))
    return setups


def _highest_rep(results: Path | None, label: str, suite: str) -> int:
    """The highest rep number in the result file of `label` among rows made by `suite` (0 when there are none)."""
    try:
        rows = read_rows(label, results)
    except BenchError:
        return 0
    return max([r["rep"] for r in rows if r.get("suite") == suite and isinstance(r.get("rep"), int)], default=0)


def run_suite(name, *, setups, reps=1, interrupt_after=None, budget=None, executable=None, db_path=None, results=None,
              scenarios_root=None, suites_root=None, live=None, out=None, sandbox=True, keep_work=False, append=False) -> dict:
    """Every scenario of the suite on every setup, interleaved: one rep of one scenario on each setup in turn, the setup that
    goes first rotating from one rep to the next. Nothing is run if a scenario, a label or a folder is wrong. The budget is
    checked between complete rounds (one scenario and rep on every setup). Returns {rows, spent, stopped, reason}; `stopped`
    says why the suite ended early (the rows so far are kept) and `reason` is "budget" or "unrecorded" (two runs in a row left
    no session in the database, so their cost cannot be tracked)."""
    suite = load_suite(name, suites_root)
    setups = [(_label(label), folder) for label, folder in setups]
    if len(setups) < 2 or len({label for label, _ in setups}) != len(setups):
        raise BenchError("give two or more --setup LABEL=DIR with different labels")
    scratches = [(label, open_scratch(folder, live=live)) for label, folder in setups]
    for _, scratch in scratches:
        refuse_old_layout(scratch)
    if len({scratch["pin_model"] for _l, scratch in scratches}) != 1:
        raise BenchError("the setups pin different models; a suite compares setups on one model (make the copies with the same --pin-model)")
    exe = executable_path(executable)
    db_path = Path(db_path) if db_path is not None else sources.DB_PATH
    scenarios = [load_scenario(n, scenarios_root) for n in suite["scenarios"]]
    if reps < 1 or reps > 20:
        raise BenchError("--reps must be 1 to 20")
    if interrupt_after is not None and interrupt_after <= 0:
        raise BenchError("--interrupt-after must be positive")
    if budget is not None and budget <= 0:
        raise BenchError("--budget must be positive")
    offset = max(_highest_rep(results, label, suite["name"]) for label, _ in setups)
    if offset and not append:
        raise BenchError(f"the result files already hold rows of suite {suite['name']!r} for these labels; use --append to add "
                         f"more (rep numbers continue after {offset}) or another label")
    rows, spent, cell, misses = [], 0.0, 0, 0

    def stop(text, reason):
        if out:
            print(text, file=out, flush=True)
        return {"rows": rows, "spent": round(spent, 6), "stopped": text, "reason": reason}

    for scenario in scenarios:
        for n in range(1, reps + 1):
            rep = offset + n
            if budget is not None and spent >= budget:
                return stop(f"stopped before {scenario['id']} r{rep}: spent ${spent:.2f} of the ${budget:.2f} budget", "budget")
            shift = cell % len(scratches)
            cell += 1
            for label, scratch in scratches[shift:] + scratches[:shift]:
                row = _run_one(scenario, rep, label=label, scratch=scratch, exe=exe, db_path=db_path, interrupt_after=interrupt_after,
                               results=results, extra={"suite": suite["name"]}, sandbox=sandbox, keep_work=keep_work, live=live)
                rows.append(row)
                spent += row.get("cost") or 0.0
                if out:
                    print(f"[{label}] {summary_line(row)}", file=out, flush=True)
                misses = misses + 1 if not row.get("sessions") else 0
                if misses >= 2:
                    return stop("stopped: two runs in a row left no session in the database, so runs are not being recorded "
                                "and their cost cannot be tracked", "unrecorded")
    return {"rows": rows, "spent": round(spent, 6), "stopped": None, "reason": None}


def run_probes(agents, *, label, config, executable=None, db_path=None, results=None, live=None, out=None, sandbox=True,
               keep_work=False) -> list[dict]:
    label = _label(label)
    if not agents:
        raise BenchError("name at least one agent")
    scratch = open_scratch(config, live=live)
    refuse_old_layout(scratch)
    exe = executable_path(executable)
    db_path = Path(db_path) if db_path is not None else sources.DB_PATH
    rows = []
    for agent in agents:
        row = one_rep(scenario=None, agent=agent, prompt=PROBE_PROMPT, label=label, rep=1, scratch=scratch, exe=exe,
                      db_path=db_path, model=scratch["pin_model"], name=f"probe-{agent}", timeout=300, auto=False, sandbox=sandbox, live=live)
        row["scenario"], row["probe_agent"] = "probe", agent
        row["check"] = {"passed": None, "detail": "probe"}
        save_row(row, results, keep_work)
        rows.append(row)
        if out:
            print(summary_line(row), file=out, flush=True)
    return rows


def summary_line(row: dict) -> str:
    check = row.get("check", {})
    state = "-" if check.get("passed") is None else ("pass" if check["passed"] else "FAIL")
    first = row.get("first_turn_tokens") or {}
    return (f"{row['scenario']}{'/' + row['probe_agent'] if row.get('probe_agent') else ''} r{row['rep']} "
            f"cost ${row.get('cost', 0):.4f} turns {row.get('turns', 0)} sessions {row.get('sessions', 0)} "
            f"{row.get('wall_seconds', 0)}s check {state} first-turn {first} "
            f"{('error: ' + row['error']) if row.get('error') else ''}").strip()


# ---- the report ----

METRICS = (("wall_seconds", "seconds"), ("cost", "cost $"), ("turns", "turns"), ("calls", "agent calls"),
           ("input", "input"), ("output", "output"), ("reasoning", "reasoning"), ("cache_read", "cache read"),
           ("cache_write", "cache write"), ("max_context", "max context"))


def read_rows(label: str, results: Path | None = None) -> list[dict]:
    results = Path(results) if results is not None else RESULTS
    path = results / f"{_label(label)}.jsonl"
    if not path.is_file():
        raise BenchError(f"no results for {label!r} in {results}")
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _group(rows: list[dict]) -> dict:
    groups: dict = {}
    for row in rows:
        key = row.get("scenario", "?") + (f"/{row['probe_agent']}" if row.get("probe_agent") else "")
        groups.setdefault(key, []).append(row)
    return groups


def _value(row: dict, metric: str):
    tokens = row.get("tokens") or {}
    if metric == "calls":
        return sum((row.get("agent_calls") or {}).values())
    if metric in tokens:
        return tokens[metric]
    return row.get(metric)


# What a token of each kind costs relative to a plain input token, whatever the model (the ratios `cost` uses; a cache
# write is counted at the five-minute price).
WEIGHTS = {"input": 1.0, "cache_read": 0.1, "cache_write": 1.25, "output": 5.0}


def weighted_tokens(row: dict) -> float | None:
    """The run's tokens counted at those ratios (reasoning tokens as output), or None when the row has no token counts."""
    tokens = row.get("tokens")
    if not isinstance(tokens, dict):
        return None
    counts = {}
    for name in ("input", "cache_read", "cache_write", "output"):
        value = tokens.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            return None
        counts[name] = value
    reasoning = tokens.get("reasoning")
    if isinstance(reasoning, (int, float)) and not isinstance(reasoning, bool) and reasoning > 0:
        counts["output"] += reasoning
    return sum(WEIGHTS[name] * value for name, value in counts.items())


def summarize(rows: list[dict]) -> dict:
    """Medians over reps of every metric, the check pass rate and, for a probe, the first-turn prompt tokens."""
    result = {"reps": len(rows)}
    for metric, _ in METRICS:
        values = [v for v in (_value(r, metric) for r in rows) if isinstance(v, (int, float))]
        result[metric] = statistics.median(values) if values else None
    checks = [r["check"]["passed"] for r in rows if isinstance(r.get("check"), dict) and r["check"].get("passed") is not None]
    result["passed"] = f"{sum(1 for c in checks if c)}/{len(checks)}" if checks else "-"
    first = [(r.get("first_turn_tokens") or {}) for r in rows]
    names = sorted({n for f in first for n in f})
    result["first_turn"] = {n: statistics.median([f[n] for f in first if n in f]) for n in names}
    recoveries = [r["recovery"] for r in rows if isinstance(r.get("recovery"), dict)]
    result["recovery"] = (f"{sum(1 for x in recoveries if x.get('resumed_ok'))}/{len(recoveries)} ok, "
                          f"{statistics.median([x['nodes_dispatched_again'] for x in recoveries])} dispatched again") if recoveries else "-"
    return result


def _fmt(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float) and abs(value) < 100 and value != int(value):
        return f"{value:.4f}" if abs(value) < 1 else f"{value:.1f}"
    return f"{value:,.0f}" if isinstance(value, (int, float)) else str(value)


def percent(old, new) -> str:
    if old is None or new is None:
        return "-"
    if old == 0:
        return "0%" if new == 0 else "new"
    return f"{(new - old) / abs(old) * 100:+.1f}%"


def report(label: str, other: str | None = None, results: Path | None = None) -> str:
    first = _group(read_rows(label, results))
    second = _group(read_rows(other, results)) if other else None
    lines = []
    for key in sorted(first if second is None else set(first) | set(second)):
        a = summarize(first[key]) if key in first else None
        b = summarize(second[key]) if second is not None and key in second else None
        lines.append(f"== {key}   reps {a['reps'] if a else 0}" + (f" / {b['reps'] if b else 0}" if second is not None else ""))
        head = f"{'metric':<14}{label:>16}" + (f"{other:>16}{'change':>10}" if second is not None else "")
        lines.append(head)
        for metric, title in METRICS:
            av, bv = (a or {}).get(metric), (b or {}).get(metric)
            if av is None and bv is None:
                continue
            line = f"{title:<14}{_fmt(av):>16}"
            if second is not None:
                line += f"{_fmt(bv):>16}{percent(av, bv):>10}"
            lines.append(line)
        pa, pb = (a or {}).get("passed", "-"), (b or {}).get("passed", "-")
        lines.append(f"{'check passed':<14}{pa:>16}" + (f"{pb:>16}" if second is not None else ""))
        for name in sorted(set((a or {}).get("first_turn", {})) | set((b or {}).get("first_turn", {}))):
            av, bv = (a or {}).get("first_turn", {}).get(name), (b or {}).get("first_turn", {}).get(name)
            line = f"{'1st ' + name:<14}{_fmt(av):>16}"
            if second is not None:
                line += f"{_fmt(bv):>16}{percent(av, bv):>10}"
            lines.append(line)
        if (a or {}).get("recovery", "-") != "-" or (b or {}).get("recovery", "-") != "-":
            lines.append(f"{'recovery':<14}{(a or {}).get('recovery', '-'):>16}" +
                         (f"{(b or {}).get('recovery', '-'):>16}" if second is not None else ""))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---- command line ----

def _run_flags(parser) -> None:
    parser.add_argument("--keep-work", action="store_true",
                        help="keep each run's work folder (the model's, no hidden checks); delete them before another run")
    parser.add_argument("--no-sandbox", action="store_true", help="run the host without the macOS sandbox profile")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="agentgraph bench", description="Reproducible toolkit bench on a scratch config.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("scratch-config", help="copy the live config into a scratch folder")
    p.add_argument("--to", required=True)
    p.add_argument("--pin-model", default=DEFAULT_PIN, help=f"model for every agent and preset (default {DEFAULT_PIN}); 'none' keeps them")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("plain-config", help="a scratch folder of plain OpenCode: no agents, prompts, plugins or skills")
    p.add_argument("--to", required=True)
    p.add_argument("--pin-model", default=DEFAULT_PIN, help=f"model to pin (default {DEFAULT_PIN}); 'none' keeps the live one")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("run", help="run scenarios")
    p.add_argument("scenarios", nargs="+")
    p.add_argument("--label", required=True)
    p.add_argument("--config")
    p.add_argument("--reps", type=int, default=1)
    p.add_argument("--interrupt-after", type=float)
    p.add_argument("--budget", type=float, help="stop starting new reps once this many dollars were spent")
    p.add_argument("--results", type=Path)
    _run_flags(p)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("probe", help="first-turn prompt tokens of each agent")
    p.add_argument("agents", nargs="+")
    p.add_argument("--label", required=True)
    p.add_argument("--config")
    p.add_argument("--results", type=Path)
    _run_flags(p)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("report", help="a table per scenario; two labels give the changes")
    p.add_argument("labels", nargs="+")
    p.add_argument("--results", type=Path)
    p = sub.add_parser("suite", help="run a suite on two or more setups, interleaved")
    p.add_argument("name")
    p.add_argument("--setup", action="append", default=[], metavar="LABEL=DIR", help="a label and its scratch folder (repeat)")
    p.add_argument("--reps", type=int, default=1)
    p.add_argument("--interrupt-after", type=float)
    p.add_argument("--budget", type=float, help="one total in dollars over all setups; no run starts once it is reached")
    p.add_argument("--results", type=Path)
    p.add_argument("--append", action="store_true", help="add to result files that already hold rows of this suite and label")
    _run_flags(p)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("card", help="a scorecard of two labels (text; --html for a page)")
    p.add_argument("labels", nargs=2, metavar="LABEL")
    p.add_argument("--html", type=Path, metavar="FILE")
    p.add_argument("--suite")
    p.add_argument("--results", type=Path)
    sub.add_parser("scenarios", help="list the scenarios")
    sub.add_parser("fingerprint", help="hashes of the live config files and ledgers (to show a bench left them alone)")
    args = parser.parse_args(argv)
    if args.command in ("run", "probe", "suite"):
        note = sandbox_note(not args.no_sandbox)
        if note:
            print(note, file=sys.stderr)
    try:
        if args.command == "scratch-config":
            info = scratch_config(args.to, pin_model=None if args.pin_model == "none" else args.pin_model)
            print(json.dumps(info) if args.json else
                  f"scratch config at {info['config']}\nprompt hash {info['prompt_hash']} ({info['prompt_files']} prompt files, "
                  f"{info['files_copied']} files copied, model pinned to {info['pin_model']})")
        elif args.command == "plain-config":
            info = plain_config(args.to, pin_model=None if args.pin_model == "none" else args.pin_model)
            print(json.dumps(info) if args.json else
                  f"plain config at {info['config']}\nsettings kept: {', '.join(info['keys']) or 'none'} (agent {PLAIN_AGENT}, "
                  f"model pinned to {info['pin_model']})")
        elif args.command == "suite":
            result = run_suite(args.name, setups=parse_setups(args.setup), reps=args.reps, interrupt_after=args.interrupt_after,
                               budget=args.budget, results=args.results, out=None if args.json else sys.stdout,
                               sandbox=not args.no_sandbox, keep_work=args.keep_work, append=args.append)
            if args.json:
                print(json.dumps(result))
            elif not result["stopped"]:
                labels = [s.partition("=")[0] for s in args.setup]
                print(f"done: {len(result['rows'])} runs, ${result['spent']:.2f}. Next: agentgraph bench card {labels[0]} {labels[1]} --suite {args.name}")
            if result["stopped"]:
                return EXIT_UNRECORDED if result["reason"] == "unrecorded" else EXIT_BUDGET
        elif args.command == "card":
            from . import benchcard
            sys.stdout.write(benchcard.run_card(args.labels[0], args.labels[1], results=args.results, suite=args.suite, html_file=args.html))
        elif args.command == "run":
            rows = run_scenarios(args.scenarios, label=args.label, config=args.config, reps=args.reps,
                                 interrupt_after=args.interrupt_after, budget=args.budget, results=args.results,
                                 out=None if args.json else sys.stdout, sandbox=not args.no_sandbox, keep_work=args.keep_work)
            if args.json:
                print(json.dumps(rows))
        elif args.command == "probe":
            rows = run_probes(args.agents, label=args.label, config=args.config, results=args.results,
                              out=None if args.json else sys.stdout, sandbox=not args.no_sandbox, keep_work=args.keep_work)
            if args.json:
                print(json.dumps(rows))
        elif args.command == "report":
            if len(args.labels) > 2:
                raise BenchError("report takes one or two labels")
            sys.stdout.write(report(args.labels[0], args.labels[1] if len(args.labels) > 1 else None, args.results))
        elif args.command == "fingerprint":
            print(json.dumps(live_fingerprint()))
        else:
            print("\n".join(list_scenarios()))
    except BenchError as exc:
        print(f"bench: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
