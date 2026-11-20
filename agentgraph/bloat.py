"""Measure the fixed text each agent's turn costs, its dead weight and what accumulates; keep a size budget.

Read-only, except `--set-baseline`, which writes one file into the ledger home. Prompt texts from the preset file
are measured, never printed: the output names a source (a key path or a file) and its size, nothing of its text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

from . import sources

TOKEN_RULE = "tokens are estimated as characters / 4"
BUDGET_CHARS = 48_000     # per agent, about 12,000 tokens of fixed text a turn
GROWTH = 0.10             # more than 10% over an agent's baseline size is growth worth a proposal
SESSIONS = 20             # an agent's last 20 sessions decide which of its skills it loads
MIN_PARAGRAPH = 80        # shorter paragraphs (headings, one-liners) are not counted as duplicates
BASELINE = "bloat-baseline.json"
# Each ledger home belongs to one preset (the improve-orchestration skills name these homes).
HOME_PRESETS = {"memory": "ultra-fast-frontier", "memory-o2": "ultra-fast", "memory-fast": "fast",
                "memory-normal": "normal"}
_FILE_LIMIT = 4 * 1024 * 1024
_WALK_LIMIT = 100_000
_PRESET_DIR = re.compile(r"[A-Za-z0-9_-]+")
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t#]*$", re.M)
_PATH = re.compile(r"(?<![\w.:/~-])(?:~/|/)(?:[A-Za-z0-9._@+-]+/)+[A-Za-z0-9._@+-]*")
_MAX_BACKUPS = 48         # skillset.MAX_BACKUPS (not imported: that module pulls in the whole editor)


def tokens(chars: int) -> int:
    return (chars + 3) // 4


@dataclass(frozen=True)
class Paths:
    config: Path         # the plugin's preset file
    prompts: Path        # the plugin's prompt folder (<agent>.md, <agent>_append.md, <preset>/...)
    agents_md: Path      # the instructions file every session reads
    skills: Path         # installed skills
    agents: Path         # OpenCode agent files
    structure: Path      # structure descriptions (<group>/<agent>.md)
    backups: Path        # undo records and backups
    orchestration: Path  # holds the memory homes and proposal folders
    project: Path        # holds .opencode/artifacts and .opencode/graph

    @classmethod
    def under(cls, root: Path, project: Path, structure: Path | None = None) -> "Paths":
        root = Path(root).expanduser()
        return cls(root / "oh-my-opencode-slim.json", root / "oh-my-opencode-slim", root / "AGENTS.md",
                   root / "skills", root / "agents", structure or root / "orchestration" / "structure",
                   root / "backups", root / "orchestration", Path(project).expanduser())

    def to_json(self) -> dict:
        return {key: str(value) for key, value in asdict(self).items()}

    @classmethod
    def from_json(cls, data: dict) -> "Paths":
        return cls(**{field.name: Path(data[field.name]) for field in fields(cls)})


def default_paths(project: Path | None = None) -> Paths:
    return Paths.under(sources.CONFIG_DIR, project or Path.cwd(),
                       Path(__file__).resolve().parent.parent / "structure")


# ---------------------------------------------------------------- reading


def _text(path: Path) -> str | None:
    """A regular file's text (a symlink is followed, as the plugin and OpenCode follow it), or None."""
    try:
        info = os.stat(path)
        if not stat.S_ISREG(info.st_mode) or info.st_size > _FILE_LIMIT:
            return None
        with open(path, "rb") as stream:
            return stream.read(_FILE_LIMIT).decode("utf-8", "replace")
    except OSError:
        return None


def _presets(config: Path) -> tuple[dict, str | None]:
    text = _text(config)
    if text is None:
        return {}, None
    try:
        data = json.loads(text)
    except ValueError:
        return {}, None
    presets = data.get("presets") if isinstance(data, dict) else None
    active = data.get("preset") if isinstance(data, dict) and isinstance(data.get("preset"), str) else None
    if not isinstance(presets, dict):
        return {}, active
    return {name: {agent: spec for agent, spec in agents.items() if isinstance(spec, dict)}
            for name, agents in presets.items() if isinstance(agents, dict)}, active


def _skills(skills_dir: Path) -> dict[str, int]:
    """{installed skill: characters of the name and description the skill list shows for it}."""
    from .snapshot_json import skills_document
    return {row["name"]: len(row["name"]) + len(row["description"] or "")
            for row in skills_document(skills_dir=skills_dir)}


def _resolve(listed, installed: dict) -> list[str]:
    """The installed skills a list gives an agent (`*` all, `!name` takes one away)."""
    values = [value for value in listed if isinstance(value, str)] if isinstance(listed, list) else []
    denied = {value[1:] for value in values if value.startswith("!")}
    if "*" in denied:
        return []
    chosen = list(installed) if "*" in values else [v for v in values if not v.startswith("!")]
    return [name for name in dict.fromkeys(chosen) if name in installed and name not in denied]


def _agent_files(agents_dir: Path) -> dict[str, dict]:
    """{name: {"text", "skills" (list or None), "description"}} of the agent files OpenCode loads."""
    from . import agentfiles
    found = {}
    try:
        entries = sorted(os.listdir(agents_dir))[:500]
    except OSError:
        return found
    for entry in entries:
        if not entry.endswith(".md") or entry.startswith("."):
            continue
        text = _text(Path(agents_dir) / entry)
        if text is None:
            continue
        data = text.encode("utf-8")
        parsed = agentfiles.parse(data) if agentfiles.is_added(data) else None
        found[entry[:-3]] = {"text": text, "skills": agentfiles.skills_of(parsed["permissions"]) if parsed else None,
                             "description": parsed["description"] if parsed else ""}
    return found


def _structure(structure: Path, agent: str) -> tuple[str, str] | None:
    try:
        groups = sorted(path for path in Path(structure).iterdir() if path.is_dir() and not path.is_symlink())
    except OSError:
        return None
    for group in groups:
        text = _text(group / f"{agent}.md")
        if text is not None:
            return f"structure/{group.name}/{agent}.md", text
    return None


def _prompt_file(paths: Paths, preset: str, name: str) -> tuple[str, str] | None:
    """The first of <prompts>/<preset>/<name> and <prompts>/<name>, as the plugin looks them up."""
    folders = ([paths.prompts / preset] if _PRESET_DIR.fullmatch(preset) else []) + [paths.prompts]
    for folder in folders:
        text = _text(folder / name)
        if text is not None:
            return f"{paths.prompts.name}/{folder.relative_to(paths.prompts).as_posix()}/{name}".replace("/./", "/"), text
    return None


# ---------------------------------------------------------------- fixed text


def _texts(paths: Paths, presets: dict, installed: dict, files: dict, instructions: str | None):
    """{preset: {agent: [(source, text or None, chars)]}}: the fixed text of each agent's turn, by source."""
    result = {}
    for preset, agents in presets.items():
        rows = {}
        for agent, spec in agents.items():
            parts = []
            if instructions is not None:
                parts.append((paths.agents_md.name, instructions, len(instructions)))
            if isinstance(spec.get("prompt"), str):
                parts.append((f"presets.{preset}.{agent}.prompt", spec["prompt"], len(spec["prompt"])))
            elif found := _prompt_file(paths, preset, f"{agent}.md"):
                parts.append((found[0], found[1], len(found[1])))
            if found := _prompt_file(paths, preset, f"{agent}_append.md"):
                parts.append((found[0], found[1], len(found[1])))
            if agent == "orchestrator":
                for other, other_spec in agents.items():
                    if other != agent and isinstance(other_spec.get("orchestratorPrompt"), str):
                        text = other_spec["orchestratorPrompt"]
                        parts.append((f"presets.{preset}.{other}.orchestratorPrompt", text, len(text)))
                described = [s["description"] for o, s in agents.items()
                             if o != agent and isinstance(s.get("description"), str)]
                described += [info["description"] for info in files.values() if info["description"]]
                if described:
                    parts.append((f"agent descriptions ({len(described)})", None, sum(map(len, described))))
            skills = _resolve(spec.get("skills"), installed)
            if skills:
                parts.append((f"skill descriptions ({len(skills)})", None, sum(installed[s] for s in skills)))
            if found := _structure(paths.structure, agent):
                parts.append((found[0], None, len(found[1])))
            rows[agent] = parts
        for agent, info in files.items():
            if agent in rows:
                continue
            parts = [(paths.agents_md.name, instructions, len(instructions))] if instructions is not None else []
            parts.append((f"agents/{agent}.md", info["text"], len(info["text"])))
            skills = _resolve(info["skills"] or [], installed)
            if skills:
                parts.append((f"skill descriptions ({len(skills)})", None, sum(installed[s] for s in skills)))
            if found := _structure(paths.structure, agent):
                parts.append((found[0], None, len(found[1])))
            rows[agent] = parts
        result[preset] = rows
    return result


def _row(parts) -> dict:
    chars = sum(part[2] for part in parts)
    ordered = sorted(parts, key=lambda part: -part[2])
    return {"chars": chars, "tokens": tokens(chars),
            "sources": [{"source": source, "chars": size} for source, _, size in ordered],
            "largest": {"source": ordered[0][0], "chars": ordered[0][2]} if ordered else None}


def fixed(paths: Paths, preset: str | None = None) -> dict:
    """{preset: {agent: row}} of fixed text (all presets, or one)."""
    presets, _ = _presets(paths.config)
    if preset is not None:
        presets = {preset: presets.get(preset, {})}
    texts = _texts(paths, presets, _skills(paths.skills), _agent_files(paths.agents), _text(paths.agents_md))
    return {name: {agent: _row(parts) for agent, parts in rows.items()} for name, rows in texts.items()}


# ---------------------------------------------------------------- dead weight


def _loaded_skills(db: Path, agents: list[str], sessions: int) -> dict[str, dict]:
    """{agent: {"sessions": n, "loaded": set}} from the agent's last `sessions` sessions (aggregates only)."""
    result = {}
    with sources._connect(db) as conn:
        for agent in agents:
            ids = [row[0] for row in conn.execute(
                "SELECT id FROM session_v2 WHERE agent = ? ORDER BY time_created DESC LIMIT ?", (agent, sessions))]
            loaded = set()
            for start in range(0, len(ids), 200):
                chunk = ids[start:start + 200]
                marks = ",".join("?" * len(chunk))
                for (data,) in conn.execute(
                        f"SELECT data FROM session_message WHERE type = 'assistant' AND session_id IN ({marks}) "
                        "AND instr(data, '\"skill\"') > 0", chunk):
                    loaded.update(_skill_calls(data))
            result[agent] = {"sessions": len(ids), "loaded": loaded}
    return result


def _skill_calls(data) -> set[str]:
    try:
        message = json.loads(data)
    except (TypeError, ValueError):
        return set()
    found = set()
    content = message.get("content") if isinstance(message, dict) else None
    for part in content if isinstance(content, list) else []:
        if isinstance(part, dict) and part.get("type") == "tool" and part.get("name") == "skill":
            state = part.get("state")
            given = state.get("input") if isinstance(state, dict) else None
            name = (given.get("name") or given.get("id")) if isinstance(given, dict) else None
            if isinstance(name, str):
                found.add(name)
    return found


def _unused(presets: dict, files: dict, installed: dict, db: Path | None, sessions: int) -> dict:
    lists = {(preset, agent): _resolve(spec.get("skills"), installed)
             for preset, agents in presets.items() for agent, spec in agents.items()}
    lists.update({("(agent files)", agent): _resolve(info["skills"], installed)
                  for agent, info in files.items() if info["skills"] is not None})
    named = {value for agents in presets.values() for spec in agents.values()
             for value in (spec.get("skills") if isinstance(spec.get("skills"), list) else [])}
    named |= {value for info in files.values() for value in info["skills"] or []}
    unlisted = sorted(name for name in installed if name not in named)
    if db is None:
        return {"known": False, "why": "unknown: no --db given", "sessions": sessions, "agents": [],
                "unlisted": unlisted}
    try:
        loads = _loaded_skills(db, sorted({agent for _, agent in lists}), sessions)
    except (sqlite3.Error, OSError) as exc:
        return {"known": False, "why": f"unknown: {exc}", "sessions": sessions, "agents": [], "unlisted": unlisted}
    rows = []
    for (preset, agent), skills in sorted(lists.items()):
        seen = loads.get(agent, {"sessions": 0, "loaded": set()})
        if not skills:
            continue
        unused = [skill for skill in skills if skill not in seen["loaded"]] if seen["sessions"] else None
        rows.append({"preset": preset, "agent": agent, "sessions": seen["sessions"], "listed": len(skills),
                     "unused": unused, "unused_chars": sum(installed[s] for s in unused or [])})
    return {"known": True, "sessions": sessions, "agents": rows, "unlisted": unlisted}


def _paragraphs(text: str) -> list[str]:
    found = (" ".join(chunk.split()) for chunk in re.split(r"\n[ \t]*\n", text.replace("\r", "")))
    return [chunk for chunk in found if len(chunk) >= MIN_PARAGRAPH]


def _duplicates(texts: dict) -> list[dict]:
    """Paragraphs that appear more than once among one preset's prompt texts, grouped by the sources sharing them."""
    merged = {}
    for preset, rows in texts.items():
        where = defaultdict(list)  # paragraph hash -> sources, in order
        size = {}
        agents_of = defaultdict(set)
        unique = {}
        for agent, parts in rows.items():
            for source, text, _ in parts:
                if text is None:
                    continue
                agents_of[source].add(agent)
                unique.setdefault(source, text)
        for source, text in unique.items():
            for paragraph in _paragraphs(text):
                key = hashlib.sha256(paragraph.encode("utf-8")).hexdigest()[:12]
                where[key].append(source)
                size[key] = len(paragraph)
        groups = defaultdict(list)
        for key, found in where.items():
            if len(found) > 1:
                groups[tuple(sorted(found))].append(key)
        for found, keys in groups.items():
            same = len(set(found)) < len(found) or any(
                len({s for s in found if agent in agents_of[s]}) > 1 for agent in rows)
            shown = tuple(source.replace(f"presets.{preset}.", "presets.*.", 1) for source in found)
            item = merged.setdefault((shown, tuple(sorted(keys))), {
                "sources": list(shown), "paragraphs": len(keys), "chars": sum(size[k] for k in keys),
                "saving": sum(size[k] for k in keys) * (len(found) - 1), "same_agent": False,
                "ids": sorted(keys)[:5], "presets": []})
            item["presets"].append(preset)
            item["same_agent"] = item["same_agent"] or same
    return sorted(merged.values(), key=lambda item: (-item["saving"], item["sources"]))


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.relative_to(folder)
        return True
    except ValueError:
        return False


def _foreign_sections(instructions: str | None, paths: Paths) -> list[dict]:
    """Sections of the instructions file that name a path outside both the config folder and the project."""
    if not instructions:
        return []
    home = Path.home()
    mine = {form for folder in (paths.agents_md.parent, paths.project)
            for form in (Path(os.path.normpath(folder.expanduser().absolute())), folder.expanduser().resolve())}
    heads = [(m.start(), len(m.group(1)), m.group(2)) for m in _HEADING.finditer(instructions)]
    found = []
    for index, (start, level, heading) in enumerate(heads):
        stop = heads[index + 1][0] if index + 1 < len(heads) else len(instructions)
        end = next((s for s, l, _ in heads[index + 1:] if l <= level), len(instructions))
        named = []
        for match in _PATH.finditer(instructions, start, stop):
            raw = match.group(0).rstrip(".,;:")
            path = Path(home, raw[2:]) if raw.startswith("~/") else Path(raw)
            if len(path.parts) < 3:
                continue
            forms = {Path(os.path.normpath(path)), Path(os.path.realpath(path))}
            if any(_inside(form, folder) or _inside(folder, form) for form in forms for folder in mine):
                continue
            named.append(raw)
        if named:
            found.append({"heading": heading.strip(), "chars": end - start, "paths": list(dict.fromkeys(named))})
    return found


# ---------------------------------------------------------------- accumulation


def _walk(root: Path) -> tuple[int, int, bool]:
    """(bytes, files, truncated) under `root`, never following symlinks."""
    total = count = 0
    try:
        info = os.lstat(root)
    except OSError:
        return 0, 0, False
    if not stat.S_ISDIR(info.st_mode):
        return (info.st_size, 1, False) if stat.S_ISREG(info.st_mode) else (0, 0, False)
    for folder, _, names in os.walk(root, followlinks=False):
        for name in names:
            try:
                info = os.lstat(os.path.join(folder, name))
            except OSError:
                continue
            total += info.st_size
            count += 1
            if count >= _WALK_LIMIT:
                return total, count, True
    return total, count, False


def _children(folder: Path) -> list[Path]:
    try:
        return sorted(path for path in Path(folder).iterdir())
    except OSError:
        return []


def _real_dir(path: Path) -> bool:
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


def _lines(path: Path) -> int:
    try:
        with open(path, "rb") as stream:
            return sum(chunk.count(b"\n") for chunk in iter(lambda: stream.read(1 << 16), b""))
    except OSError:
        return 0


def _homes(orchestration: Path) -> tuple[list[dict], list[dict]]:
    homes, proposals = [], []
    for folder in _children(orchestration):
        if not _real_dir(folder):
            continue
        if folder.name == "memory" or folder.name.startswith("memory-"):
            files = [{"name": path.name, "bytes": os.lstat(path).st_size,
                      **({"rows": _lines(path)} if path.suffix == ".jsonl" else {})}
                     for path in _children(folder) if path.is_file() and not path.is_symlink()]
            homes.append({"home": folder.name, "preset": HOME_PRESETS.get(folder.name),
                          "bytes": sum(f["bytes"] for f in files), "files": files})
        elif folder.name == "proposals" or folder.name.startswith("proposals-"):
            size, count, _ = _walk(folder)
            proposals.append({"folder": folder.name, "proposals": count, "bytes": size})
    return homes, proposals


def _backups(backups: Path) -> dict:
    """Sizes of the undo records and backups, and what the toolkit's own pruning would drop from them."""
    entries = [path for path in _children(backups) if not path.is_symlink()]
    total, files, truncated = _walk(backups)
    drop = []                                      # (bytes) of each copy pruning would remove
    records = defaultdict(list)                     # agent undo records, across days
    toolkit = 0
    for day in entries:
        if not _real_dir(day):
            continue
        if day.name.endswith("-agent-files"):
            toolkit += 1
            for record in _children(day):
                found = re.fullmatch(r"(.+)-[0-9a-f]{12}", record.name)
                if found and _real_dir(record) and [p.name for p in _children(record)] == ["record.json"]:
                    records[found.group(1)].append((day.name, os.lstat(record).st_mtime_ns, record))
        elif day.name.endswith("-skill-files"):
            toolkit += 1
            kinds = defaultdict(list)
            for copy in _children(day):
                if _real_dir(copy) and "-" in copy.name:
                    kinds[copy.name.rsplit("-", 1)[0]].append((os.lstat(copy).st_mtime_ns, copy))
            for copies in kinds.values():
                copies.sort()
                for _, old in copies[:max(len(copies) - _MAX_BACKUPS, 0)]:
                    if sorted(p.name for p in _children(old)) in (["SKILL.md"], ["SKILL.md", "record.json"]):
                        drop.append(_walk(old)[0])
        else:
            folders = [day] + [sub for sub in _children(day) if _real_dir(sub)]
            numbered_any = False
            for folder in folders:
                numbered = defaultdict(list)
                for path in _children(folder):
                    found = re.fullmatch(r"(.+)\.(\d{4,})", path.name)
                    if found and path.is_file() and not path.is_symlink():
                        numbered[found.group(1)].append((int(found.group(2)), path))
                for copies in numbered.values():
                    numbered_any = True
                    copies.sort()
                    drop += [os.lstat(p).st_size for _, p in copies[:max(len(copies) - _MAX_BACKUPS, 0)]]
            toolkit += numbered_any or day.name.endswith("-skills-edit")
    for kept in records.values():
        kept.sort(key=lambda item: item[:2])
        drop += [_walk(old)[0] for _, _, old in kept[:max(len(kept) - _MAX_BACKUPS, 0)]]
    return {"entries": len(entries), "bytes": total, "files": files, "truncated": truncated,
            "toolkit_folders": toolkit, "hand_made": len(entries) - toolkit,
            "prune_drops": len(drop), "prune_bytes": sum(drop)}


def _project(project: Path) -> dict:
    artifacts, graphs = [], []
    for task in _children(project / ".opencode" / "artifacts"):
        if _real_dir(task):
            size, count, truncated = _walk(task)
            artifacts.append({"task": task.name, "bytes": size, "files": count, "truncated": truncated})
    for graph in _children(project / ".opencode" / "graph"):
        if graph.suffix == ".json" and graph.is_file() and not graph.is_symlink():
            graphs.append({"task": graph.stem, "bytes": os.lstat(graph).st_size})
    artifacts.sort(key=lambda item: -item["bytes"])
    graphs.sort(key=lambda item: -item["bytes"])
    return {"project": str(project), "artifacts": artifacts, "graphs": graphs}


# ---------------------------------------------------------------- baseline and signal


def _home(home: Path | None) -> Path:
    return (home or Path(os.environ.get("AGENTGRAPH_HOME", "~/.config/opencode/orchestration/memory"))).expanduser()


def home_preset(home: Path, paths: Paths) -> str | None:
    preset = HOME_PRESETS.get(Path(home).name)
    presets, active = _presets(paths.config)
    if preset in presets:
        return preset
    return active if active in presets else next(iter(presets), None)


def load_baseline(home: Path) -> dict | None:
    """The stored baseline, or None when there is none (or it cannot be read as one)."""
    text = _text(Path(home) / BASELINE)
    if text is None:
        return None
    try:
        data = json.loads(text)
        Paths.from_json(data["paths"])
    except (ValueError, KeyError, TypeError):
        return None
    good = (isinstance(data.get("preset"), str) and isinstance(data.get("agents"), dict)
            and all(isinstance(k, str) and type(v) is int for k, v in data["agents"].items())
            and type(data.get("budget_chars")) is int and isinstance(data.get("growth"), (int, float))
            and not isinstance(data.get("growth"), bool))
    return data if good else None


def set_baseline(home: Path, paths: Paths, *, preset: str | None = None, budget: int = BUDGET_CHARS,
                 growth: float = GROWTH, now: float | None = None) -> dict:
    preset = preset or home_preset(home, paths)
    if preset is None:
        raise ValueError(f"no preset in {paths.config}")
    rows = fixed(paths, preset)[preset]
    if not rows:
        raise ValueError(f"preset {preset!r} has no agents in {paths.config}")
    data = {"version": 1, "time": time.time() if now is None else now, "preset": preset,
            "budget_chars": budget, "growth": growth, "paths": paths.to_json(),
            "agents": {agent: row["chars"] for agent, row in rows.items()}}
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".bloat-", dir=home)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(data, indent=2) + "\n")
        os.replace(name, home / BASELINE)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return data


def current(home: Path) -> tuple[dict, dict] | None:
    """(baseline, {agent: chars now}) for the baseline's preset and paths, or None without a baseline."""
    baseline = load_baseline(home)
    if baseline is None:
        return None
    rows = fixed(Paths.from_json(baseline["paths"]), baseline["preset"])[baseline["preset"]]
    return baseline, rows


def signal(home: Path) -> dict:
    """Whether an agent's fixed text is over its budget or grew too much since the baseline. No baseline, no nag.

    An agent's limit is the budget, or its baseline size when that was already larger: setting the baseline
    accepts today's sizes, and growth beyond `growth` of the baseline still counts."""
    try:
        found = current(home)
    except (OSError, ValueError) as exc:
        return {"due": False, "budget_chars": None, "error": str(exc)}
    if found is None:
        return {"due": False, "budget_chars": None, "note": "no budget set (agentgraph bloat --set-baseline sets one)"}
    baseline, rows = found
    budget, growth = baseline["budget_chars"], baseline["growth"]
    flagged = []
    for agent, row in rows.items():
        base = baseline["agents"].get(agent)
        reasons = []
        if row["chars"] > max(budget, base or 0):
            reasons.append(f"over the {budget:,} budget")
        if base and row["chars"] > base * (1 + growth):
            reasons.append(f"+{(row['chars'] - base) / base:.0%} since the baseline")
        if reasons:
            flagged.append({"agent": agent, "chars": row["chars"], "tokens": row["tokens"], "baseline": base,
                            "reasons": reasons, "largest": row["largest"]})
    flagged.sort(key=lambda item: (-(item["chars"] - (item["baseline"] or 0)), item["agent"]))
    return {"due": bool(flagged), "preset": baseline["preset"], "budget_chars": budget, "growth": growth,
            "baseline_time": baseline.get("time"), "agents": flagged}


def signal_line(report: dict) -> str | None:
    """The line `record` and `signals` print, or None (nothing to say: not due, or no budget set)."""
    if report.get("error"):
        return f"bloat: not measured ({report['error']})"
    if not report.get("due"):
        return None
    top = report["agents"][0]
    line = (f"improvement due: bloat: {top['agent']} {top['chars']:,} chars (~{top['tokens']:,} tokens) "
            f"{' and '.join(top['reasons'])}")
    if top["largest"]:
        line += f"; largest source {top['largest']['source']} ({top['largest']['chars']:,} chars)"
    more = len(report["agents"]) - 1
    return line + (f"; {more} more agent{'s' * (more != 1)} (agentgraph bloat)" if more else "")


# ---------------------------------------------------------------- the report


def measure(paths: Paths, *, db: Path | None = None, sessions: int = SESSIONS, home: Path | None = None) -> dict:
    presets, active = _presets(paths.config)
    installed = _skills(paths.skills)
    files = _agent_files(paths.agents)
    instructions = _text(paths.agents_md)
    texts = _texts(paths, presets, installed, files, instructions)
    rows = {name: {agent: _row(parts) for agent, parts in agents.items()} for name, agents in texts.items()}
    largest = Counter()
    for agents in texts.values():
        for parts in agents.values():
            for source, _, size in parts:
                if not source.startswith(("agent descriptions", "skill descriptions")):
                    largest[source] = max(largest[source], size)
    homes, proposals = _homes(paths.orchestration)
    baseline = load_baseline(home) if home is not None else None
    return {
        "tokens": TOKEN_RULE, "active_preset": active, "presets": rows,
        "agent_files": sorted(files),
        "largest_sources": [{"source": s, "chars": c} for s, c in largest.most_common(5)],
        "budget": None if baseline is None else {"chars": baseline["budget_chars"], "growth": baseline["growth"],
                                                 "preset": baseline["preset"], "time": baseline.get("time")},
        "defaults": {"budget_chars": BUDGET_CHARS, "growth": GROWTH, "sessions": sessions,
                     "min_paragraph": MIN_PARAGRAPH},
        "dead": {"skills": _unused(presets, files, installed, db, sessions), "duplicates": _duplicates(texts),
                 "foreign_sections": _foreign_sections(instructions, paths)},
        "accumulation": {"homes": homes, "proposals": proposals, "backups": _backups(paths.backups),
                         **_project(paths.project)},
        "not_counted": "the plugin's built-in prompts (an agent with no prompt text or file uses one) and tool schemas",
    }


def _kb(size: int) -> str:
    return f"{size / 1024:.1f} KB" if size >= 1024 else f"{size} B"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'s' * (count != 1)}"


def _shape(row: dict) -> tuple:
    return row["chars"], tuple(s["chars"] for s in row["sources"])


def _print(report: dict, signal_report: dict | None) -> None:
    print(f"Fixed text each turn, per preset and agent ({report['tokens']}; presets.*. is the preset shown):")
    order = sorted(report["presets"], key=lambda name: name != report["active_preset"])
    first = None
    for preset in order:
        agents = report["presets"][preset]
        mark = " (active)" if preset == report["active_preset"] else ""
        same = [a for a, row in agents.items() if first and a in first[1] and _shape(row) == _shape(first[1][a])]
        print(f"{preset}{mark}:")
        for agent, row in sorted(agents.items(), key=lambda item: -item[1]["chars"]):
            if agent in same:
                continue
            top = row["sources"][:4]
            rest = row["sources"][4:]
            split = ", ".join(f"{s['source'].replace(f'presets.{preset}.', 'presets.*.', 1)} {s['chars']:,}"
                              for s in top)
            if rest:
                split += f", {len(rest)} more {sum(s['chars'] for s in rest):,}"
            print(f"  {agent:<18} {row['chars']:>7,} chars ~{row['tokens']:>6,} tokens: {split}")
        if same:
            print(f"  {_plural(len(same), 'other agent')}: the same sizes as in {first[0]}")
        first = first or (preset, agents)
    print("Largest sources: " + ", ".join(f"{s['source']} ({s['chars']:,})" for s in report["largest_sources"]))
    budget = report["budget"]
    if budget is None:
        print(f"Budget: no budget set (agentgraph bloat --set-baseline sets one; default "
              f"{report['defaults']['budget_chars']:,} chars an agent, growth over {report['defaults']['growth']:.0%})")
    else:
        print(f"Budget: {budget['chars']:,} chars an agent in {budget['preset']}, growth over {budget['growth']:.0%} "
              "since the baseline")
        line = signal_line(signal_report or {})
        print(line or "bloat: within budget")
    dead = report["dead"]
    skills = dead["skills"]
    print("Dead weight:")
    if not skills["known"]:
        print(f"  skills not loaded in the last {skills['sessions']} sessions: {skills['why']}")
    else:
        print(f"  skills not loaded in each agent's last {skills['sessions']} sessions (sessions are not split by preset):")
        merged = {}
        for row in skills["agents"]:
            key = (row["agent"], row["listed"], row["sessions"], tuple(row["unused"] or ()), row["unused"] is None)
            merged.setdefault(key, (row, []))[1].append(row["preset"])
        for row, presets in merged.values():
            where = f"{row['agent']} [{', '.join(presets)}]"
            if row["unused"] is None:
                print(f"    {where}: unknown (no sessions)")
            elif row["unused"]:
                print(f"    {where}: {len(row['unused'])} of {row['listed']} unused over "
                      f"{_plural(row['sessions'], 'session')} ({row['unused_chars']:,} chars): {', '.join(row['unused'])}")
    print("  skills no agent's list names: " + (", ".join(skills["unlisted"]) or "none"))
    if dead["duplicates"]:
        print("  duplicated paragraphs (by source; ids are hashes, the text is not shown):")
        for item in dead["duplicates"][:10]:
            where = " in one agent's text" if item["same_agent"] else ""
            print(f"    {_plural(item['paragraphs'], 'paragraph')}, {item['chars']:,} chars, saving {item['saving']:,}{where}: "
                  f"{', '.join(item['sources'])} [{', '.join(item['presets'])}]")
        if len(dead["duplicates"]) > 10:
            print(f"    and {len(dead['duplicates']) - 10} more (--json)")
    else:
        print("  duplicated paragraphs: none")
    for item in dead["foreign_sections"]:
        print(f"  instructions section naming another project: \"{item['heading']}\" ({item['chars']:,} chars): "
              f"{', '.join(item['paths'])}")
    acc = report["accumulation"]
    print("Accumulation:")
    for home in acc["homes"]:
        files = ", ".join(f"{f['name']} {_kb(f['bytes'])}" + (f" ({_plural(f['rows'], 'row')})" if "rows" in f else "")
                          for f in home["files"])
        print(f"  {home['home']}: {_kb(home['bytes'])}: {files or 'empty'}")
    for folder in acc["proposals"]:
        print(f"  {folder['folder']}: {_plural(folder['proposals'], 'proposal')}, {_kb(folder['bytes'])}")
    b = acc["backups"]
    print(f"  backups: {b['entries']} entries, {b['files']} files, {_kb(b['bytes'])} ({b['toolkit_folders']} written "
          f"by this toolkit, {b['hand_made']} by hand, never pruned); prune would drop {b['prune_drops']} "
          f"({_kb(b['prune_bytes'])})")
    print(f"  {acc['project']}/.opencode: artifacts of {_plural(len(acc['artifacts']), 'task')} "
          f"({_kb(sum(a['bytes'] for a in acc['artifacts']))}), {_plural(len(acc['graphs']), 'graph file')} "
          f"({_kb(sum(g['bytes'] for g in acc['graphs']))})")
    for item in acc["artifacts"][:5]:
        print(f"    artifacts/{item['task']}: {_kb(item['bytes'])}, {item['files']} files")
    print(f"Not counted: {report['not_counted']}.")


def _fraction(text: str) -> float:
    value = float(text)
    if not 0 <= value <= 10:
        raise argparse.ArgumentTypeError("growth is a fraction such as 0.1")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentgraph bloat", description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--db", type=Path, help="OpenCode session database (read-only); without it unused skills are unknown")
    parser.add_argument("--sessions", type=int, default=SESSIONS, help=f"sessions per agent to look at (default {SESSIONS})")
    parser.add_argument("--home", type=Path, help="ledger home holding the baseline (default: $AGENTGRAPH_HOME or "
                                                  "~/.config/opencode/orchestration/memory)")
    parser.add_argument("--set-baseline", action="store_true", help="store today's sizes and the budget in the home")
    parser.add_argument("--budget", type=int, default=BUDGET_CHARS, help=f"characters an agent (default {BUDGET_CHARS})")
    parser.add_argument("--growth", type=_fraction, default=GROWTH, help=f"growth since the baseline (default {GROWTH})")
    parser.add_argument("--preset", help="preset the baseline measures (default: the home's preset)")
    parser.add_argument("--opencode-dir", type=Path, help="config folder every path below defaults into")
    parser.add_argument("--project", type=Path, help="project holding .opencode (default: current folder)")
    for flag, help_text in (("config", "plugin preset file"), ("prompts", "plugin prompt folder"),
                            ("agents-md", "instructions file"), ("skills-dir", "skills folder"),
                            ("agents-dir", "agent files folder"), ("structure", "structure folder"),
                            ("backups", "backups folder"), ("orchestration", "folder of memory homes and proposals")):
        parser.add_argument(f"--{flag}", type=Path, help=help_text)
    args = parser.parse_args(argv)
    project = (args.project or Path.cwd()).expanduser()
    paths = Paths.under(args.opencode_dir, project) if args.opencode_dir else default_paths(project)
    overrides = {"config": args.config, "prompts": args.prompts, "agents_md": args.agents_md,
                 "skills": args.skills_dir, "agents": args.agents_dir, "structure": args.structure,
                 "backups": args.backups, "orchestration": args.orchestration}
    paths = replace(paths, **{key: value.expanduser() for key, value in overrides.items() if value is not None})
    home = _home(args.home)
    try:
        if args.set_baseline:
            if args.budget <= 0:
                raise ValueError("--budget must be positive")
            data = set_baseline(home, paths, preset=args.preset, budget=args.budget, growth=args.growth)
            print(f"baseline set in {home / BASELINE}: {data['preset']}, {len(data['agents'])} agents, budget "
                  f"{data['budget_chars']:,} chars, growth over {data['growth']:.0%}")
            return 0
        report = measure(paths, db=args.db, sessions=max(args.sessions, 1), home=home)
        signal_report = signal(home) if report["budget"] is not None else None
        if args.json:
            print(json.dumps({**report, "signal": signal_report}, allow_nan=False))
        else:
            _print(report, signal_report)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"bloat: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
