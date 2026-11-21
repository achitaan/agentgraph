"""One source for the three orchestrator policy files (`<preset>/orchestrator_append.md`), so a policy change is made once.

The source (`config/policy/orchestrator_append.src.md`) is plain Markdown: the shared text once, and what differs
between presets in marker blocks. A marker is a whole line, nothing else on it:

    <!-- presets: fast, normal, ultra-fast -->   the presets, before any text (once)
    <!-- only: fast, normal -->                   the lines up to the next end are written to those presets only
    <!-- end -->
    <!-- no-final-newline: ultra-fast -->         that preset's file does not end with a newline
    <!-- note: any words -->                      a remark for the editor; written to no file

Blocks do not nest. Any other line is written to every preset. A line that starts like a marker but is not one is an
error with its line number, never text. No template engine, no values: a difference is a block.

    python3 -m agentgraph.policygen build --out DIR          # DIR/<preset>/orchestrator_append.md
    python3 -m agentgraph.policygen check [--against ROOT]   # byte for byte against ROOT (the live folder); exit 0 if same
    python3 -m agentgraph.policygen split --from ROOT --to SOURCE   # make the source from existing files
    python3 -m agentgraph.policygen stats

`build` writes only under --out, and into the live folder only when --out names it and --live is given. `check` and
`stats` print counts and line numbers, never the text of a policy line."""
from __future__ import annotations

import argparse
import difflib
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .paths import REPO

FILE = "orchestrator_append.md"
PRESETS = ("fast", "normal", "ultra-fast")
SOURCE = REPO / "config" / "policy" / "orchestrator_append.src.md"
NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
_LIKE = re.compile(r"<!--\s*(only|presets|no-final-newline|note|end)\b")
_NAMES = r"([a-z0-9][a-z0-9-]*(?:, [a-z0-9][a-z0-9-]*)*)"
_MARKER = {
    "only": re.compile(rf"<!-- only: {_NAMES} -->"),
    "presets": re.compile(rf"<!-- presets: {_NAMES} -->"),
    "no-final-newline": re.compile(rf"<!-- no-final-newline: {_NAMES} -->"),
    "note": re.compile(r"<!-- note: .* -->"),
    "end": re.compile(r"<!-- end -->"),
}
NOTE = "<!-- note: Source of every preset's orchestrator_append.md. Edit here, then run python3 -m agentgraph.policygen build. -->"


class PolicyError(Exception):
    pass


@dataclass
class Policy:
    presets: list[str]
    lines: dict[str, list[str]]
    no_final_newline: set[str] = field(default_factory=set)
    shared: int = 0


def _lines(text: str) -> list[str]:
    parts = text.split("\n")
    if parts[-1] == "":
        parts.pop()
    return parts


def _check_name(name: str) -> str:
    if not NAME.fullmatch(name):
        raise PolicyError(f"not a preset name: {name!r}")
    return name


def parse(text: str) -> Policy:
    presets: list[str] | None = None
    lines: dict[str, list[str]] = {}
    no_final: set[str] = set()
    shared, block, opened = 0, None, 0
    for n, line in enumerate(_lines(text), 1):
        like = _LIKE.match(line)
        if like is None:
            if presets is None:
                raise PolicyError(f"line {n}: text before the presets marker")
            if block is None:
                shared += 1
            for name in presets if block is None else block:
                lines[name].append(line)
            continue
        kind = like.group(1)
        match = _MARKER[kind].fullmatch(line)
        if match is None:
            raise PolicyError(f"line {n}: malformed {kind} marker")
        if kind == "note":
            continue
        if kind == "end":
            if block is None:
                raise PolicyError(f"line {n}: end without only")
            block = None
            continue
        names = match.group(1).split(", ")
        if kind == "presets":
            if presets is not None:
                raise PolicyError(f"line {n}: presets given twice")
            if block is not None or shared:
                raise PolicyError(f"line {n}: presets must come before any text")
            presets = names
            if len(set(presets)) != len(presets):
                raise PolicyError(f"line {n}: a preset is listed twice")
            lines = {name: [] for name in presets}
            continue
        if presets is None:
            raise PolicyError(f"line {n}: {kind} before the presets marker")
        unknown = [name for name in names if name not in lines]
        if unknown:
            raise PolicyError(f"line {n}: unknown preset {unknown[0]}")
        if len(set(names)) != len(names):
            raise PolicyError(f"line {n}: a preset is listed twice")
        if kind == "no-final-newline":
            if block is not None:
                raise PolicyError(f"line {n}: no-final-newline inside a block")
            no_final.update(names)
        else:
            if block is not None:
                raise PolicyError(f"line {n}: only inside a block (opened on line {opened})")
            block, opened = names, n
    if block is not None:
        raise PolicyError(f"line {opened}: only block never closed")
    if presets is None:
        raise PolicyError("no presets marker")
    for name in no_final:
        if not lines[name]:
            raise PolicyError(f"{name} has no lines, so it cannot lack a final newline")
    return Policy(presets, lines, no_final, shared)


def render(policy: Policy) -> dict[str, bytes]:
    out = {}
    for name in policy.presets:
        _check_name(name)
        body = "\n".join(policy.lines[name])
        if policy.lines[name] and name not in policy.no_final_newline:
            body += "\n"
        out[name] = body.encode("utf-8")
    return out


def build_source(source: Path) -> dict[str, bytes]:
    try:
        text = source.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise PolicyError(f"cannot read source {source}: {exc}") from None
    return render(parse(text))


def split(files: dict[str, bytes]) -> str:
    """The source text that `build` turns back into exactly these files."""
    presets = list(files)
    lines, no_final = {}, []
    for name, data in files.items():
        _check_name(name)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise PolicyError(f"{name}: not UTF-8") from None
        lines[name] = _lines(text)
        if text and not text.endswith("\n"):
            no_final.append(name)
        for n, line in enumerate(lines[name], 1):
            if _LIKE.match(line):
                raise PolicyError(f"{name} line {n}: starts like a marker, so it cannot be split")
    base = presets[0]
    maps = []
    for name in presets[1:]:
        blocks = difflib.SequenceMatcher(None, lines[base], lines[name], autojunk=False).get_matching_blocks()
        maps.append({a + k: b + k for a, b, size in blocks for k in range(size)})
    anchors = [i for i in range(len(lines[base])) if all(i in m for m in maps)]
    out = [NOTE, f"<!-- presets: {', '.join(presets)} -->"]
    if no_final:
        out.append(f"<!-- no-final-newline: {', '.join(no_final)} -->")
    prev = {name: 0 for name in presets}

    def gap(ends: dict[str, int]) -> None:
        groups: dict[tuple[str, ...], list[str]] = {}
        for name in presets:
            groups.setdefault(tuple(lines[name][prev[name]:ends[name]]), []).append(name)
        for content, names in groups.items():
            if not content:
                continue
            if len(names) == len(presets):
                out.extend(content)
            else:
                out.extend([f"<!-- only: {', '.join(names)} -->", *content, "<!-- end -->"])

    for i in anchors:
        ends = {base: i} | {name: m[i] for name, m in zip(presets[1:], maps)}
        gap(ends)
        out.append(lines[base][i])
        prev = {name: end + 1 for name, end in ends.items()}
    gap({name: len(lines[name]) for name in presets})
    text = "\n".join(out) + "\n"
    if render(parse(text)) != {name: data for name, data in files.items()}:
        raise PolicyError("split did not reproduce the files exactly; nothing written")
    return text


def differing(built: list[str], other: list[str]) -> tuple[int, list[str]]:
    """(count, where): the lines of `other` that differ from `built`, as "line N", "lines N-M" or "missing after line N" in `other`."""
    count, where = 0, []
    matcher = difflib.SequenceMatcher(None, built, other, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        count += max(i2 - i1, j2 - j1)
        if j1 == j2:
            where.append(f"missing after line {j1}")
        else:
            where.append(f"line {j1 + 1}" if j2 - j1 == 1 else f"lines {j1 + 1}-{j2}")
    return count, where


def live_root() -> Path:
    return Path(os.environ.get("OPENCODE_CONFIG_DIR") or Path.home() / ".config" / "opencode") / "oh-my-opencode-slim"


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def write(files: dict[str, bytes], out: Path, live: bool = False) -> list[Path]:
    root, written = out.resolve(), []
    targets = []
    for name in files:
        _check_name(name)
        target = (root / name / FILE).resolve()
        if not _inside(target, root):
            raise PolicyError(f"{name}: would write outside {out}")
        if _inside(target, live_root().resolve()) and not live:
            raise PolicyError(f"{target.parent} is the live config; name it with --out and add --live to write there")
        targets.append(target)
    for target, data in zip(targets, files.values()):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        written.append(target)
    return written


def cmd_build(args) -> int:
    for path in write(build_source(args.source), Path(args.out), args.live):
        print(f"wrote {path}")
    return 0


def cmd_check(args) -> int:
    root, built, ok = Path(args.against) if args.against else live_root(), build_source(args.source), True
    for name, data in built.items():
        path = root / _check_name(name) / FILE
        try:
            other = path.read_bytes()
        except OSError:
            print(f"{name}: missing ({path})")
            ok = False
            continue
        if other == data:
            print(f"{name}: same")
            continue
        ok = False
        try:
            count, where = differing(_lines(data.decode("utf-8")), _lines(other.decode("utf-8")))
        except UnicodeDecodeError:
            print(f"{name}: not UTF-8")
            continue
        print(f"{name}: {count} lines differ" + (f" ({', '.join(where)})" if where else ", final newline differs"))
    return 0 if ok else 1


def cmd_split(args) -> int:
    root, target = Path(args.src_root), Path(args.to)
    if target.exists() and not args.force:
        raise PolicyError(f"{target} exists; add --force to replace it")
    files = {}
    for name in args.presets.split(","):
        path = root / _check_name(name) / FILE
        try:
            files[name] = path.read_bytes()
        except OSError as exc:
            raise PolicyError(f"cannot read {path}: {exc}") from None
    text = split(files)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="")
    print(f"wrote {target}")
    return 0


def cmd_stats(args) -> int:
    try:
        policy = parse(args.source.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise PolicyError(f"cannot read source {args.source}: {exc}") from None
    print(f"shared lines: {policy.shared}")
    for name in policy.presets:
        print(f"{name}: {len(policy.lines[name])} lines ({len(policy.lines[name]) - policy.shared} only here or in some presets)")
    for i, a in enumerate(policy.presets):
        for b in policy.presets[i + 1:]:
            print(f"{a} vs {b}: {differing(policy.lines[a], policy.lines[b])[0]} lines differ")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m agentgraph.policygen", description=__doc__.split("\n")[0])
    parser.add_argument("--source", type=Path, default=SOURCE, help=f"the source file (default {SOURCE.relative_to(REPO)})")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("build", help="write DIR/<preset>/orchestrator_append.md")
    p.add_argument("--out", required=True)
    p.add_argument("--live", action="store_true", help="allow --out to be the live config folder")
    p = sub.add_parser("check", help="compare a build with the files under ROOT, byte for byte")
    p.add_argument("--against", help="default: the live oh-my-opencode-slim folder")
    p = sub.add_parser("split", help="make the source from existing files")
    p.add_argument("--from", dest="src_root", required=True)
    p.add_argument("--to", required=True)
    p.add_argument("--presets", default=",".join(PRESETS))
    p.add_argument("--force", action="store_true", help="replace an existing source file")
    sub.add_parser("stats", help="shared lines, lines per preset, differences between presets")
    args = parser.parse_args(argv)
    try:
        return {"build": cmd_build, "check": cmd_check, "split": cmd_split, "stats": cmd_stats}[args.command](args)
    except PolicyError as exc:
        print(f"policygen: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
