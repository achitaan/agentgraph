"""Data layer for agentgraph: config + graph files + OpenCode DB -> Snapshot.

Standard library only. Nothing here raises on bad or missing input; problems
are returned as warning strings. The database is only ever opened read-only.
"""
from __future__ import annotations

import json
import heapq
from collections import Counter
import os
import re
import sqlite3
import stat
import time
from pathlib import Path

from .contract import Agent, Event, Node, Objective, Session, Snapshot, TaskGraph, POOL, group_of
from .contract import Artifact, ArtifactInput
from .contract import GRAPH_LIMITS, MAX_NODE_LIST

CONFIG_DIR = Path.home() / ".config" / "opencode"
DB_PATH = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
MODELS_CACHE = Path.home() / ".cache" / "opencode" / "models.json"
MAX_GRAPH_BYTES = 2 * 1024 * 1024
MAX_GRAPH_FILES = 200


def pretty_model(model_id: str) -> str:
    """'openai/gpt-6.1-sol-ultrafast' -> 'GPT-6.1 Sol Ultrafast'."""
    name = str(model_id).split("/", 1)[-1]
    parts = name.split("-")
    out: list[str] = []
    i = 0
    while i < len(parts):
        part = parts[i]
        if part == "gpt" and i + 1 < len(parts):
            out.append(f"GPT-{parts[i + 1]}")
            i += 2
            continue
        if part.isdigit():
            digits = [part]
            while i + 1 < len(parts) and parts[i + 1].isdigit():
                i += 1
                digits.append(parts[i])
            out.append(".".join(digits))
        elif part:
            out.append(part.capitalize())
        i += 1
    return " ".join(out)


# --- roster ---------------------------------------------------------------


def _model_spec(spec) -> tuple[str, str] | None:
    """Return (model id, variant) from a preset entry, or None."""
    if not isinstance(spec, dict):
        return None
    model = spec.get("model")
    variant = spec.get("variant") or ""
    if isinstance(model, list):
        model = model[0] if model else None
        if isinstance(model, dict):
            variant = model.get("variant") or variant
            model = model.get("id")
    if not isinstance(model, str) or not model:
        return None
    return model, str(variant)


def _agent(name: str, model: str, variant: str) -> Agent:
    return Agent(name, pretty_model(model), variant, group_of(name), POOL.get(name))


def load_roster(config_dir: Path) -> tuple[list[Agent], list[str]]:
    agents, _, warnings = _load_roster(config_dir)
    return agents, warnings


def _disabled_agents(config: dict, warnings: list[str]) -> list[str]:
    value = config.get("disabled_agents", [])
    if not isinstance(value, list):
        warnings.append("roster: disabled_agents is not a list")
        return []
    return [name for name in value if isinstance(name, str)]


def _load_roster(config_dir: Path, *, config_path: Path | None = None,
                 agents_dir: Path | None = None) -> tuple[list[Agent], list[str], list[str]]:
    """(roster, the config's disabled_agents, warnings). A disabled name is
    never in the roster, whichever preset or agent file lists it. The preset
    file is `config_path` and the agent files are in `agents_dir` (default:
    `oh-my-opencode-slim.json` and `agents/` in `config_dir`)."""
    agents: list[Agent] = []
    warnings: list[str] = []
    config_dir = Path(config_dir)
    cfg_path = Path(config_path) if config_path is not None else config_dir / "oh-my-opencode-slim.json"
    folder = Path(agents_dir) if agents_dir is not None else config_dir / "agents"
    try:
        config = json.loads(cfg_path.read_text())
        if not isinstance(config, dict):
            raise ValueError("not a JSON object")
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"roster: cannot read {cfg_path.name}: {exc}")
        config = {}

    if config:
        try:
            presets = config.get("presets") or {}
            preset = presets.get(config.get("preset")) or {}
            for name, spec in preset.items():
                parsed = _model_spec(spec)
                if parsed:
                    agents.append(_agent(name, *parsed))
                    listed = spec.get("skills") if isinstance(spec, dict) else None
                    if isinstance(listed, list):
                        agents[-1].skills = [str(skill)[:80] for skill in listed if isinstance(skill, str)][:50]
                else:
                    warnings.append(f"roster: agent '{name}' has no usable model")
            council = config.get("council") or {}
            seats = (council.get("presets") or {}).get(council.get("default_preset")) or {}
            for seat, spec in seats.items():
                parsed = _model_spec(spec)
                if parsed:
                    agents.append(_agent(f"councillor-{seat}", *parsed))
                else:
                    warnings.append(f"roster: council seat '{seat}' has no usable model")
            if not preset:
                warnings.append("roster: active preset not found in config")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"roster: malformed {cfg_path.name}: {exc}")

    # Older configs define the reviewer as a native agent file instead of a
    # preset entry. The file is optional, and the preset entry wins.
    reviewer_path = folder / "reviewer.md"
    if not any(agent.name == "reviewer" for agent in agents):
        try:
            text = reviewer_path.read_text()
        except FileNotFoundError:
            text = None
        except Exception as exc:  # noqa: BLE001
            text = None
            warnings.append(f"roster: cannot read reviewer.md: {exc}")
        if text is not None:
            front = text.split("---", 2)[1] if text.startswith("---") else text
            model = re.search(r"^model:\s*(\S+)", front, re.M)
            variant = re.search(r"^variant:\s*(\S+)", front, re.M)
            if model:
                agents.append(_agent("reviewer", model.group(1), variant.group(1) if variant else ""))
            else:
                warnings.append("roster: reviewer.md has no model line")

    # Other native agent files in agents/*.md. The model may carry its effort
    # as "provider/model#variant".
    known = {agent.name for agent in agents}
    try:
        native = sorted(folder.glob("*.md"))
    except OSError:
        native = []
    for path in native:
        name = path.stem
        if name in known:
            continue
        try:
            text = path.read_text()
        except OSError as exc:
            warnings.append(f"roster: cannot read {path.name}: {exc}")
            continue
        front = text.split("---", 2)[1] if text.startswith("---") and text.count("---") >= 2 else ""
        model = re.search(r"^model:\s*(\S+)", front, re.M)
        if not model:
            continue
        model_id, _, variant = model.group(1).strip("'\"").partition("#")
        found = re.search(r"^variant:\s*(\S+)", front, re.M)
        agents.append(_agent(name, model_id, variant or (found.group(1) if found else "")))
        known.add(name)

    disabled = _disabled_agents(config, warnings) if config else []
    agents = [agent for agent in agents if agent.name not in disabled]
    agents.sort(key=lambda a: a.name != "orchestrator")  # stable: orchestrator first
    return agents, disabled, warnings


# --- graphs ---------------------------------------------------------------


def read_graph_text(path: Path) -> str:
    """Read one bounded regular graph file without following its symlink."""
    path = Path(path)
    root = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise OSError("not a regular file")
            if info.st_size > MAX_GRAPH_BYTES:
                raise OSError(f"graph exceeds {MAX_GRAPH_BYTES} byte limit")
            with open(fd, "rb", closefd=False) as stream:
                data = stream.read(MAX_GRAPH_BYTES + 1)
            if len(data) > MAX_GRAPH_BYTES:
                raise OSError(f"graph exceeds {MAX_GRAPH_BYTES} byte limit")
            return data.decode("utf-8")
        finally:
            os.close(fd)
    finally:
        os.close(root)


def graph_files(directory: Path) -> tuple[list[Path], list[str]]:
    """Select at most 200 graphs, newest by mtime, without following file links."""
    directory = Path(directory)
    if directory.stat().st_uid != os.getuid():
        raise PermissionError(f"graph directory {directory} is not owned by the current user")
    warnings = []
    count = 0

    def entries():
        nonlocal count
        with os.scandir(directory) as scan:
            for entry in scan:
                if not entry.name.endswith(".json"):
                    continue
                try:
                    modified = entry.stat(follow_symlinks=False).st_mtime_ns
                except OSError as exc:
                    warnings.append(f"graph: skipped {entry.name}: {exc}")
                    continue
                count += 1
                yield (-modified, entry.name, Path(entry.path))

    newest = heapq.nsmallest(MAX_GRAPH_FILES, entries())
    if count > MAX_GRAPH_FILES:
        warnings.append(f"graph: skipped {count - MAX_GRAPH_FILES} older graph files in {directory} "
                        f"(limit {MAX_GRAPH_FILES}, newest by mtime first)")
    return [path for _, _, path in newest], warnings


def _num(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _int(value, default: int = 0) -> int:
    """An integer field. JSON integers stay exact; anything else is the default."""
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


def _opt_num(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _list(value) -> list:
    return list(value) if isinstance(value, list) else []


def _str(value, default="") -> str:
    try:
        return default if value is None else str(value)
    except (ValueError, RecursionError):
        return default


def _text_items(value) -> list[str]:
    return [_str(item, "None" if item is None else "") for item in _list(value)]


def _sid(value) -> str | None:
    return value if isinstance(value, str) and value else None


def _artifact_entries(value, fields: tuple[str, str]) -> list[dict]:
    """Keep only complete, well-typed handoffs; never coerce identity or revision."""
    return [entry for entry in _list(value) if isinstance(entry, dict)
            and all(isinstance(entry.get(key), str) and entry[key].strip() for key in fields)
            and type(entry.get("revision")) is int and entry["revision"] > 0]


def _capped(raw: dict, field: str, over: Counter, unique: bool = False) -> list:
    """The field's list, duplicates removed (order kept) when `unique`, and at
    most MAX_NODE_LIST entries; a cut is counted in `over`."""
    items = _list(raw.get(field))
    if unique:
        try:
            items = list(dict.fromkeys(items))
        except TypeError:  # unhashable entries; the caller drops or converts them
            pass
    if len(items) > MAX_NODE_LIST:
        over[field] += 1
        items = items[:MAX_NODE_LIST]
    return items


def _node(raw: dict, over: Counter | None = None) -> Node:
    over = Counter() if over is None else over
    pattern = raw.get("pattern")
    # A feedback_to list with any non-string entry is dropped whole, as before.
    feedback = (_capped(raw, "feedback_to", over, unique=True)
                if all(isinstance(target, str) for target in _list(raw.get("feedback_to"))) else [])
    depends_on = list(dict.fromkeys(_text_items(_list(raw.get("depends_on")))))
    if len(depends_on) > MAX_NODE_LIST:
        over["depends_on"] += 1
        depends_on = depends_on[:MAX_NODE_LIST]
    return Node(
        id=_str(raw.get("id")),
        type=_str(raw.get("type"), "implement"),
        agent=_str(raw.get("agent")),
        goal=_str(raw.get("goal")),
        status=_str(raw.get("status"), "pending"),
        depends_on=depends_on,
        scope=_text_items(_capped(raw, "scope", over)),
        done_when=_str(raw.get("done_when")),
        attempts=_int(raw.get("attempts"), 0),
        tiers=_text_items(_capped(raw, "tiers", over)),
        evidence=_str(raw.get("evidence")),
        session=_sid(raw.get("session")),
        started=_opt_num(raw.get("started")),
        finished=_opt_num(raw.get("finished")),
        baseline=_str(raw.get("baseline")),
        outputs=_str(raw.get("outputs")),
        changed_paths=_text_items(_capped(raw, "changed_paths", over)),
        failure_kind=_str(raw.get("failure_kind")),
        stalled=bool(raw.get("stalled")),
        verdict=_str(raw.get("verdict")),
        pattern=pattern if isinstance(pattern, str) else "",
        feedback_to=feedback,
        artifacts=[Artifact(entry["id"], entry["path"], entry["revision"])
                   for entry in _artifact_entries(_capped(raw, "artifacts", over), ("id", "path"))],
        inputs=[ArtifactInput(entry["from"], entry["artifact"], entry["revision"])
                for entry in _artifact_entries(_capped(raw, "inputs", over), ("from", "artifact"))],
    )


def _objective(raw: dict, over: Counter | None = None) -> Objective:
    over = Counter() if over is None else over
    return Objective(
        id=_str(raw.get("id")),
        text=_str(raw.get("text")),
        nodes=_text_items(_capped(raw, "nodes", over)),
    )


def _event(raw: dict) -> Event:
    return Event(
        time=_num(raw.get("time")),
        node=_str(raw.get("node")),
        event=_str(raw.get("event")),
        note=_str(raw.get("note")),
    )


def graph_from_raw(raw: dict, path: Path) -> TaskGraph:
    """Convert a decoded graph object with the display layer's tolerant defaults."""
    records = {}
    truncated = []
    for name, limit in GRAPH_LIMITS.items():
        items = raw.get(name)
        records[name] = items[:limit] if isinstance(items, list) else []
        if isinstance(items, list) and len(items) > limit:
            truncated.append(f"{name}: kept {limit} of {len(items)}")
    node_over: Counter = Counter()
    objective_over: Counter = Counter()
    nodes = [_node(n, node_over) for n in records["nodes"] if isinstance(n, dict)]
    objectives = [_objective(o, objective_over) for o in records["objectives"] if isinstance(o, dict)]
    truncated += [f"nodes[].{name}: kept {MAX_NODE_LIST} entries in {node_over[name]} "
                  f"node{'s' if node_over[name] != 1 else ''}"
                  for name in ("depends_on", "scope", "changed_paths", "feedback_to", "artifacts", "inputs", "tiers")
                  if node_over[name]]
    if objective_over["nodes"]:
        count = objective_over["nodes"]
        truncated.append(f"objectives[].nodes: kept {MAX_NODE_LIST} entries in {count} "
                         f"objective{'s' if count != 1 else ''}")
    return TaskGraph(
        path=str(path),
        task=_str(raw.get("task"), path.stem),
        goal=_str(raw.get("goal")),
        status=_str(raw.get("status"), "running"),
        created=_num(raw.get("created")),
        updated=_num(raw.get("updated")),
        nodes=nodes,
        events=[_event(e) for e in records["events"] if isinstance(e, dict)],
        session=_sid(raw.get("session")),
        revision=_int(raw.get("revision"), 1),
        objectives=objectives,
        amendments=_text_items(raw.get("amendments")),
        lessons=_text_items(raw.get("lessons")),
        truncated=truncated,
    )


def load_graphs(graph_dir: Path | None) -> tuple[list[TaskGraph], list[str]]:
    graphs: list[TaskGraph] = []
    warnings: list[str] = []
    if graph_dir is None:
        return graphs, warnings
    try:
        files, warnings = graph_files(graph_dir)
    except FileNotFoundError:
        return graphs, warnings
    except OSError as exc:
        return graphs, [f"graph: cannot read {graph_dir}: {exc}"]
    for path in files:
        try:
            raw = json.loads(read_graph_text(path))
            if not isinstance(raw, dict):
                raise ValueError("not a JSON object")
            graph = graph_from_raw(raw, path)
            graphs.append(graph)
            warnings.extend(f"graph: truncated {path.name}: {notice}" for notice in graph.truncated)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"graph: skipped {path.name}: {exc}")
    graphs.sort(key=lambda g: g.updated, reverse=True)
    return graphs, warnings


def find_graph_dir(start: Path, warnings: list[str] | None = None) -> Path | None:
    """Discover only current-user graph directories within the user's home."""
    try:
        current = Path(start).resolve()
        home = Path.home().resolve()
    except Exception:  # noqa: BLE001
        return None
    if not current.is_relative_to(home):
        return None
    for directory in (current, *current.parents):
        candidate = directory / ".opencode" / "graph"
        try:
            info = candidate.stat()
            if stat.S_ISDIR(info.st_mode):
                if info.st_uid == os.getuid():
                    return candidate
                if warnings is not None:
                    warnings.append(f"graph: skipped {candidate}: not owned by the current user")
        except FileNotFoundError:
            pass
        except OSError as exc:
            if warnings is not None:
                warnings.append(f"graph: skipped {candidate}: {exc}")
        if directory == home:
            break
    return None


# --- sessions -------------------------------------------------------------

_COLUMNS = (
    "id, parent_id, agent, model, title, directory, cost, tokens_input, "
    "tokens_output, time_created, time_updated, time_busy, time_idle"
)


_CACHE_COLUMNS = ("tokens_cache_read", "tokens_cache_write")


def _session(row: tuple) -> Session:
    (sid, parent, agent, model, title, directory, cost, tin, tout, created, updated, busy, idle, *cached) = row
    model_name, effort = "", ""
    if model:
        try:
            data = json.loads(model) if isinstance(model, str) else model
            if isinstance(data, dict):
                model_name = pretty_model(data["id"]) if data.get("id") else ""
                effort = str(data.get("variant") or "")
        except Exception:  # noqa: BLE001
            pass
    return Session(
        id=str(sid),
        parent_id=str(parent) if parent else None,
        agent=_str(agent),
        title=_str(title),
        model=model_name,
        effort=effort,
        busy=busy is not None and (idle is None or busy > idle),
        created=_num(created) / 1000.0,
        updated=_num(updated) / 1000.0,
        cost=_num(cost),
        # Everything sent to the model: fresh input plus what was read from or written to the cache.
        tokens_in=int(_num(tin) + sum(_num(value) for value in cached)),
        tokens_out=int(_num(tout)),
        directory=_str(directory),
    )


def _connect(db_path) -> sqlite3.Connection:
    """Open the database strictly read-only (path is percent-encoded via as_uri)."""
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=1.0)


def _read_sessions(db_path: Path, pick=None, wear=None) -> list[Session]:
    """`pick` chooses, from all sessions, the ones whose context is worth reading.
    `wear`, when given, is called with the connection and the first picked session
    (the root) to attach its `wear`."""
    conn = _connect(db_path)
    try:
        present = {row[1] for row in conn.execute("PRAGMA table_info(session_v2)")}
        extra = "".join(f", {name}" for name in _CACHE_COLUMNS if name in present)
        rows = conn.execute(f"SELECT {_COLUMNS}{extra} FROM session_v2").fetchall()
        sessions = [_session(r) for r in rows]
        if pick is not None:
            chosen = pick(sessions)
            _fill_context(conn, chosen)
            if wear is not None and chosen:
                wear(conn, chosen[0])
    finally:
        conn.close()
    return sessions


_CONTEXT_QUERY = (
    "SELECT json_extract(data, '$.tokens.input'), json_extract(data, '$.tokens.cache.read'), "
    "json_extract(data, '$.tokens.cache.write') FROM session_message "
    "WHERE session_id = ? AND type = 'assistant' AND json_extract(data, '$.tokens.input') IS NOT NULL "
    "ORDER BY seq DESC LIMIT 1"
)


def _fill_context(conn: sqlite3.Connection, sessions: list[Session]) -> None:
    """Set each session's `context`: the prompt size of its last model call.
    Left as None when the database does not record it."""
    try:
        for session in sessions:
            row = conn.execute(_CONTEXT_QUERY, (session.id,)).fetchone()
            if row is not None:
                session.context = int(sum(_num(value) for value in row))
    except Exception:  # noqa: BLE001
        pass


# --- wear: how long and how full the root session has grown ------------------

DEFAULT_WINDOW = 1_000_000
HISTORY_POINTS = 60
PER_TURN_SLICE = 20
FRESH_SHARE = 0.6
WORN_SUMMARIES = 3

_MODELS_MEMO: dict[str, tuple[int, int, dict]] = {}


def _positive_int(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if value > 0 and value == value and value != float("inf") else None


def _read_json(path: Path | None):
    try:
        return json.loads(Path(path).read_text()) if path is not None else None
    except Exception:  # noqa: BLE001
        return None


def _models_contexts(path: Path | None) -> dict:
    """`{provider: {model id: context window}}` from the models catalogue, parsed
    again only when the file's modification time or size changes."""
    if path is None:
        return {}
    try:
        info = Path(path).stat()
    except OSError:
        return {}
    key = str(path)
    memo = _MODELS_MEMO.get(key)
    if memo and memo[:2] == (info.st_mtime_ns, info.st_size):
        return memo[2]
    table: dict = {}
    raw = _read_json(path)
    if isinstance(raw, dict):
        for provider, entry in raw.items():
            models = entry.get("models") if isinstance(entry, dict) else None
            if not isinstance(models, dict):
                continue
            found = {}
            for model_id, model in models.items():
                limit = model.get("limit") if isinstance(model, dict) else None
                window = _positive_int(limit.get("context")) if isinstance(limit, dict) else None
                if window:
                    found[model_id] = window
            if found:
                table[provider] = found
    _MODELS_MEMO[key] = (info.st_mtime_ns, info.st_size, table)
    return table


def _config_window(config, provider: str | None, model_id: str) -> int | None:
    entries = config.get("providers") if isinstance(config, dict) else None
    if not isinstance(entries, dict):
        return None
    names = [provider] if provider in entries else ([] if provider else list(entries))
    for name in names:
        models = entries[name].get("models") if isinstance(entries[name], dict) else None
        model = models.get(model_id) if isinstance(models, dict) else None
        limit = model.get("limit") if isinstance(model, dict) else None
        window = _positive_int(limit.get("context")) if isinstance(limit, dict) else None
        if window:
            return window
    return None


def context_window(config, models_cache: Path | None, provider: str | None, model_id: str) -> tuple[int, bool]:
    """(window, known): from `providers.<provider>.models.<id>.limit.context` in the
    opencode config, then from the models catalogue, else 1,000,000 and not known."""
    if model_id:
        window = _config_window(config, provider, model_id)
        if window:
            return window, True
        table = _models_contexts(models_cache)
        for name in ([provider] if provider in table else ([] if provider else list(table))):
            if model_id in table[name]:
                return table[name][model_id], True
    return DEFAULT_WINDOW, False


def compaction_point(config, window: int) -> int | None:
    """Where OpenCode next summarizes: the window minus the buffer; None when
    `compaction.auto` is false. A numeric buffer is a token count (ignored when it is
    not below the window), a string like "20%" a share of the window, and absent
    (or unusable) means 15% of the window and at least 16,000."""
    compaction = config.get("compaction") if isinstance(config, dict) else None
    if not isinstance(compaction, dict):
        compaction = {}
    if compaction.get("auto") is False:
        return None
    raw = compaction.get("buffer")
    buffer = None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        if 0 <= raw < window:
            buffer = int(raw)
    elif isinstance(raw, str):
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*%\s*", raw)
        if match and 0 <= float(match.group(1)) < 100:
            buffer = int(window * float(match.group(1)) / 100)
    if buffer is None:
        buffer = min(max(16_000, int(window * 0.15)), window // 2)
    return window - buffer


_WEAR_QUERY = (
    "SELECT json_extract(data, '$.tokens.input', '$.tokens.cache.read', '$.tokens.cache.write', '$.cost'), "
    "json_extract(data, '$.model.id', '$.model.providerID'), seq FROM session_message "
    "WHERE session_id = ? AND type = 'assistant' AND json_valid(data) ORDER BY seq"
)


def _buckets(values: list[int], count: int = HISTORY_POINTS) -> list[int]:
    """At most `count` numbers: each bucket of neighbouring turns keeps its largest."""
    if len(values) <= count:
        return list(values)
    size = len(values)
    return [max(values[i * size // count:(i + 1) * size // count]) for i in range(count)]


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def session_wear(conn: sqlite3.Connection, session_id: str, config, models_cache: Path | None) -> dict | None:
    """How full and how worn a session is, or None when it made no model calls."""
    calls: list[tuple[int, float, int]] = []  # (context, cost, seq)
    turns = 0
    seqs: list[int] = []
    model: tuple = (None, None)
    for packed, ids, seq in conn.execute(_WEAR_QUERY, (session_id,)):
        turns += 1
        seqs.append(seq)
        try:
            tokens_in, cache_read, cache_write, cost = json.loads(packed)
        except Exception:  # noqa: BLE001
            continue
        if tokens_in is None:
            continue
        calls.append((int(_num(tokens_in) + _num(cache_read) + _num(cache_write)), _num(cost), seq))
        try:
            found = json.loads(ids)
            if isinstance(found, list) and isinstance(found[0], str) and found[0]:
                model = (found[0], found[1] if isinstance(found[1], str) else None)
        except Exception:  # noqa: BLE001
            pass
    if not calls:
        return None
    if model[0] is None:
        row = conn.execute("SELECT model FROM session_v2 WHERE id = ?", (session_id,)).fetchone()
        try:
            data = json.loads(row[0]) if row and isinstance(row[0], str) else None
            if isinstance(data, dict) and isinstance(data.get("id"), str):
                model = (data["id"], data.get("providerID") if isinstance(data.get("providerID"), str) else None)
        except Exception:  # noqa: BLE001
            pass
    window, known = context_window(config, models_cache, model[1], model[0] or "")
    compact_at = compaction_point(config, window)
    summary_seqs = [row[0] for row in conn.execute(
        "SELECT seq FROM session_message WHERE session_id = ? AND type = 'compaction' ORDER BY seq", (session_id,))]
    summaries = len(summary_seqs)
    since = sum(seq > summary_seqs[-1] for seq in seqs) if summaries else turns
    context = calls[-1][0]
    span = PER_TURN_SLICE if len(calls) >= 2 * PER_TURN_SLICE else len(calls) // 2
    costs = [cost for _, cost, _ in calls]
    per_turn = {"start": _mean(costs[:span]), "recent": _mean(costs[-span:]) if span else None}
    if summaries >= WORN_SUMMARIES:
        level = "worn"
    elif summaries == 0 and context < FRESH_SHARE * (compact_at if compact_at else window):
        level = "fresh"
    else:
        level = "long"
    return {
        "context": context, "window": window, "window_known": known, "compact_at": compact_at,
        "summaries": summaries, "turns": turns, "turns_since_summary": since,
        "history": _buckets([item[0] for item in calls]), "per_turn": per_turn, "level": level,
    }


def _read_checked(db_path: Path, pick=None, wear=None) -> tuple[list[Session] | None, list[str]]:
    try:
        if not Path(db_path).exists():
            return None, [f"sessions: database not found: {db_path}"]
        return _read_sessions(Path(db_path), pick, wear), []
    except Exception as exc:  # noqa: BLE001
        return None, [f"sessions: cannot read database: {exc}"]


def _dirs(directory) -> list[str]:
    if not directory:
        return []
    if isinstance(directory, (list, tuple)):
        return [str(d) for d in directory if d]
    return [str(directory)]


def top_level_session(sessions: list[Session], session_id: str | None) -> Session | None:
    """The top-level session that `session_id` belongs to (itself if it is one).

    Walks `parent_id` up. Returns None for an unknown id, a missing ancestor
    or a parent cycle, so a sub-agent id never resolves to the wrong tree.
    """
    by_id = {s.id: s for s in sessions}
    current = by_id.get(session_id) if session_id else None
    seen: set[str] = set()
    while current is not None and current.parent_id:
        if current.id in seen:
            return None
        seen.add(current.id)
        current = by_id.get(current.parent_id)
    return current


def _select(
    sessions: list[Session], root_id, directory
) -> tuple[Session | None, list[Session], list[str]]:
    roots = [s for s in sessions if not s.parent_id]
    warnings: list[str] = []
    root = None
    if root_id:
        root = next((s for s in roots if s.id == root_id), None)
        if root is None:
            root = top_level_session(sessions, root_id)
        if root is None:
            return None, [], [f"sessions: session {root_id} has no top-level session in the database"]
    else:
        dirs = _dirs(directory)
        pool = roots
        if dirs:
            same = [s for s in roots if s.directory in dirs]
            if same:
                pool = same
            elif roots:
                warnings.append(
                    f"sessions: no session matched {', '.join(dirs)}; showing the most recent session instead"
                )
        root = max(pool, key=lambda s: s.updated, default=None)
    if root is None:
        return None, [], warnings
    children: dict[str, list[Session]] = {}
    for s in sessions:
        if s.parent_id:
            children.setdefault(s.parent_id, []).append(s)
    found: list[Session] = []
    seen = {root.id}
    stack = [root.id]
    while stack:
        for child in children.get(stack.pop(), []):
            if child.id not in seen:
                seen.add(child.id)
                found.append(child)
                stack.append(child.id)
    found.sort(key=lambda s: s.created)
    return root, found, warnings


def load_sessions(
    db_path: Path, root_id: str | None = None, directory=None
) -> tuple[Session | None, list[Session], list[str]]:
    sessions, warnings = _read_checked(db_path)
    if sessions is None:
        return None, [], warnings
    return _select(sessions, root_id, directory)


# --- snapshot -------------------------------------------------------------


# Where the orchestration folder keeps its own task graphs. A session is often
# opened in another folder than the one its graph was written in.
ORCHESTRATION_GRAPH_DIR = Path(__file__).resolve().parent.parent / ".opencode" / "graph"


def _owned_elsewhere(directory: Path, known: Path | None, owners: set[str]) -> tuple[list[TaskGraph], list[str]]:
    """Graphs in `directory` that belong to one of `owners`, unless it is the
    folder already read. Other sessions' graphs there are never shown."""
    try:
        if known is not None and Path(known).resolve() == Path(directory).resolve():
            return [], []
    except OSError:
        return [], []
    graphs, warnings = load_graphs(directory)
    owned = [graph for graph in graphs if graph.session in owners]
    return owned, (warnings if owned else [])


def build_snapshot(
    config_dir: Path = CONFIG_DIR,
    graph_dir: Path | None = None,
    db_path: Path = DB_PATH,
    root_id: str | None = None,
    cwd: Path | None = None,
    now: float | None = None,
    session_graph_dir: Path | None = None,
    models_cache: Path | None = None,
    roster_config: Path | None = None,
    agents_dir: Path | None = None,
) -> Snapshot:
    """`session_graph_dir` is a second folder searched only for graphs that
    belong to the requested session or one of its ancestors. The root session's
    `wear` reads `opencode.json` from `config_dir`, and the models catalogue from
    `models_cache` (by default the user's OpenCode cache, but only for the real
    config folder, so a snapshot built from a test folder never reads it).
    The roster comes from `roster_config` (the preset file) and `agents_dir`
    when given, else from `config_dir`."""
    cwd = Path(cwd) if cwd else Path.cwd()
    discovery_warnings: list[str] = []
    if graph_dir is None:
        graph_dir = find_graph_dir(cwd, warnings=discovery_warnings)
    roster, disabled, w1 = _load_roster(config_dir, config_path=roster_config, agents_dir=agents_dir)
    graphs, w2 = load_graphs(graph_dir)

    candidates: list[str] = []
    if graph_dir is not None:
        gd = Path(graph_dir)
        if gd.parent.name == ".opencode":
            candidates.append(str(gd.parent.parent))
    candidates.append(str(cwd))

    picked: dict = {"root": None, "sessions": [], "warnings": []}

    def pick(everyone: list[Session]) -> list[Session]:
        chosen = root_id
        if not chosen and graphs and graphs[0].session:
            if any(s.id == graphs[0].session for s in everyone):
                chosen = graphs[0].session
            else:
                picked["warnings"].append(f"sessions: graph session {graphs[0].session} not found in the database")
        found_root, found, more = _select(everyone, chosen, candidates)
        picked.update(root=found_root, sessions=found)
        picked["warnings"] += more
        return ([found_root] if found_root is not None else []) + found

    if models_cache is None and Path(config_dir) == CONFIG_DIR:
        models_cache = MODELS_CACHE

    def wear(conn: sqlite3.Connection, root_session: Session) -> None:
        try:
            root_session.wear = session_wear(conn, root_session.id, _read_json(Path(config_dir) / "opencode.json"),
                                             models_cache)
        except Exception:  # noqa: BLE001
            pass

    sessions_all, w3 = _read_checked(db_path, pick, wear)
    root, sessions = picked["root"], picked["sessions"]
    w3 += picked["warnings"]
    if sessions_all is not None:
        if root_id and session_graph_dir is not None:
            parents = {s.id: s.parent_id for s in sessions_all}
            owners: set[str] = set()
            current = root_id
            while current and current in parents and current not in owners:
                owners.add(current)
                current = parents[current]
            owned, w5 = _owned_elsewhere(session_graph_dir, graph_dir, owners)
            if owned:
                graphs = sorted(graphs + owned, key=lambda g: g.updated, reverse=True)
                w2 = w2 + w5
    return Snapshot(
        now=time.time() if now is None else now,
        roster=roster,
        root=root,
        sessions=sessions,
        graphs=graphs,
        warnings=discovery_warnings + w1 + w2 + w3,
        disabled_agents=disabled,
    )
