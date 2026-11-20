"""The models in use, as evidence for the improvement loop: rules written for one generation of models can be dead
weight for the next, so when the models change after the prompts were last reviewed, one deletion is worth proposing.

`agentgraph improved` and `agentgraph bloat --set-baseline` store, with what they already store, a fingerprint of
the models: the preset's name and a hash of the agent-to-model map (each agent's model and variant). Only those
two values are kept, never the map and never anything else of the preset file, and nothing of that file is printed.
`signals` prints one line when today's fingerprint (the baseline's preset, read from the baseline's own config path)
differs from the last stored one. It is quiet when nothing was stored (no baseline was ever set, so nothing was
compared), when the preset cannot be read, or when nothing changed; the next recorded improvement stores today's
fingerprint and so clears it. Missing or unreadable files mean no fingerprint."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

PRESET = re.compile(r"[A-Za-z0-9_-]{1,64}")
HASH = re.compile(r"[0-9a-f]{64}")


def fingerprint(config: Path, preset: str) -> dict | None:
    """{"preset", "hash"} for `preset` in the preset file `config`, or None (no such file or preset, no agents, or a
    name that is not a plain preset name)."""
    from . import bloat
    if not isinstance(preset, str) or not PRESET.fullmatch(preset):
        return None
    presets, _ = bloat._presets(Path(config))
    agents = presets.get(preset)
    if not agents:
        return None
    pairs = {name: [spec.get("model") if isinstance(spec.get("model"), str) else None,
                    spec.get("variant") if isinstance(spec.get("variant"), str) else None]
             for name, spec in agents.items() if isinstance(name, str)}
    text = json.dumps(sorted(pairs.items()), ensure_ascii=True, separators=(",", ":"))
    return {"preset": preset, "hash": hashlib.sha256(text.encode("ascii")).hexdigest()}


def valid(item) -> bool:
    return (isinstance(item, dict) and set(item) == {"preset", "hash"} and isinstance(item["preset"], str)
            and PRESET.fullmatch(item["preset"]) is not None and isinstance(item["hash"], str)
            and HASH.fullmatch(item["hash"]) is not None)


def today(home: Path) -> dict | None:
    """The fingerprint of the home's baseline preset in the baseline's config file now; None without a baseline."""
    from . import bloat
    baseline = bloat.load_baseline(home)
    if baseline is None:
        return None
    return fingerprint(Path(baseline["paths"]["config"]), baseline["preset"])


def stored_field(home: Path) -> dict:
    """{"models": fingerprint} for a record that stores it (an improvement), or {} (no baseline, or none readable)."""
    try:
        found = today(home)
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    return {"models": found} if found else {}


def last_stored(home: Path, improvements: list[dict]) -> dict | None:
    """The latest stored fingerprint: the newest improvement's, or the baseline's when that is newer."""
    from . import bloat
    best, when = None, None
    baseline = bloat.load_baseline(home)
    if baseline is not None and valid(baseline.get("models")) and isinstance(baseline.get("time"), (int, float)):
        best, when = baseline["models"], baseline["time"]
    for item in improvements:
        if valid(item.get("models")) and (when is None or item["time"] >= when):
            best, when = item["models"], item["time"]
    return best


def signal(home: Path, improvements: list[dict]) -> dict:
    """{"due", "stored", "now"}: due when both fingerprints exist and differ."""
    try:
        stored, now = last_stored(home, improvements), today(home)
    except (OSError, ValueError, KeyError, TypeError):
        return {"due": False, "stored": None, "now": None}
    return {"due": stored is not None and now is not None and stored != now, "stored": stored, "now": now}


def signal_lines(report: dict) -> list[str]:
    if not report or not report.get("due"):
        return []
    old, new = report["stored"]["preset"], report["now"]["preset"]
    where = f"preset {new}" if old == new else f"preset {old} to {new}"
    return [f"improvement due: models: the models changed since the prompts were last reviewed ({where}); rules "
            "written for the old models may be dead weight: propose one deletion"]
