"""Read and safely edit installed SKILL.md files; all locations are overridable."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import unicodedata

from . import skillset

SKILLS = skillset.sources.CONFIG_DIR / "skills"
BACKUPS = skillset.BACKUPS
CONFIG = skillset.CONFIG
SkillfilesError = skillset.SkillsetError
_ID = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_KEY = re.compile(r"""("(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[^ \t#'"[\]{},?:&*!|>%@`-][^:]*?):([ \t]+|$)(.*)\Z""")
_NEWLINE = re.compile(r"\r\n|\r|\n")
_LINE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$")


def _id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise SkillfilesError("invalid skill id: use 1–64 lowercase letters, digits or '-', starting with a letter")


def _text(value, label, limit=None, *, single=False):
    if not isinstance(value, str):
        raise SkillfilesError(f"{label} must be text")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise SkillfilesError(f"{label} is not UTF-8") from exc
    if limit is not None and len(value) > limit:
        raise SkillfilesError(f"{label} must be at most {limit} characters")
    if single and any(c in value for c in "\r\n\u2028\u2029"):
        raise SkillfilesError(f"{label} must be one line")
    if any(unicodedata.category(c) in {"Cc", "Cs"} and c not in "\r\n\t" for c in value):
        raise SkillfilesError(f"{label} must not contain control characters")


def _directory(path, *, create=False):
    """Check every component, including configured roots (never follow symlinks)."""
    path = Path(os.path.abspath(path))
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if create:
            try:
                current.mkdir()
            except FileExistsError:
                pass
        info = current.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise SkillfilesError(f"directory must not be a symlink or special file: {current}")
    return path


@contextmanager
def _operation(skills, *, config=None, backups=None):
    try:
        root = _directory(skills)
        if backups is not None:
            # Backups must not appear inside the installed catalogue.
            target = Path(os.path.abspath(backups))
            if target == root or root in target.parents:
                raise SkillfilesError("backups must be outside the skills folder")
        paths = [root / "SKILL.md"]
        if config is not None:
            config = Path(config)
            _directory(config.parent)
            paths.append(config)
        with skillset._locked(*paths):
            yield root
    except OSError as exc:
        raise SkillfilesError(f"skill file operation failed: {exc}") from exc


def _tree(folder):
    _directory(folder)
    files = []

    def visit(directory):
        for child in sorted(directory.iterdir()):
            mode = child.lstat().st_mode
            if stat.S_ISDIR(mode):
                visit(child)
            elif stat.S_ISREG(mode):
                if child != folder / "SKILL.md":
                    files.append(child.relative_to(folder).as_posix())
            else:
                raise SkillfilesError(f"skill files must be regular files or directories, not symlinks: {child}")

    visit(folder)
    return files


def _scalar(raw):
    """Read one of the two editable-format scalars and retain its literal suffix."""
    value = raw.rstrip(" \t")
    if not value:
        raise SkillfilesError("frontmatter: empty values or nested mappings are not supported")
    if value.startswith("'"):
        match = re.fullmatch(r"'((?:[^']|'')*)'([ \t]+#.*)?", value)
        if match:
            return match[1].replace("''", "'"), raw[match.end(1) + 1:]
    elif value.startswith('"'):
        try:
            result, end = json.JSONDecoder().raw_decode(value)
            if isinstance(result, str) and re.fullmatch(r"(?:[ \t]+#.*)?", value[end:]):
                return result, raw[end:]
        except ValueError:
            pass
    else:
        # Plain scalar comments start only after whitespace.
        value = re.split(r"[ \t]+#", value, maxsplit=1)[0].rstrip(" \t")
        if (value and value[0] not in "[]{}&*!|>'\"%@`#?,:-"
                and not re.search(r":[ \t]|:$", value)
                and value.lower() not in {"null", "~", "true", "false", ".nan", ".inf", "-.inf"}
                and not re.fullmatch(r"[-+]?\d[\d_.eE+-]*", value)):
            return value, raw[len(value):]
    raise SkillfilesError("frontmatter: unsupported or malformed name/description scalar")


def _parse(text):
    _text(text, "SKILL.md")
    lines = _LINE.findall(text)
    clean = lambda line: line.rstrip("\r\n")
    if not lines or clean(lines[0]).rstrip(" \t") != "---":
        raise SkillfilesError("frontmatter must start with ---")
    end = next((i for i in range(1, len(lines)) if clean(lines[i]).rstrip(" \t") == "---"), None)
    if end is None:
        raise SkillfilesError("frontmatter is missing its closing ---")
    fields, entries = {}, []
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    for i in range(1, end):
        if match := _KEY.fullmatch(clean(lines[i])):
            key = match[1].rstrip(" \t")
            if key.startswith(("'", '"')):
                try:
                    key, _ = _scalar(key)
                except SkillfilesError:
                    pass  # The key and its value remain opaque.
            entries.append((i, key, match))
    for (i, key, match), (next_i, _, _) in zip(entries, [*entries[1:], (end, None, None)]):
        if key not in {"name", "description"}:
            # Report the whole source block, not a parsed YAML value. Preserve
            # punctuation, line endings, nested structures, comments and blanks.
            fields[key] = fields.get(key, "") + text[offsets[i]:offsets[next_i]]
            continue
        if key in fields:
            raise SkillfilesError(f"frontmatter has duplicate key: {key!r}")
        scalar = match[3]
        block = re.fullmatch(r"[>|][-+]?([ \t]*(?:[ \t]+#.*)?)", scalar)
        last = i + 1
        if block and key == "description":
            value, last = _block_description(lines, last, next_i)
            suffix = block[1]
        else:
            value, suffix = _scalar(scalar)
        if any(line.strip() and not line.lstrip().startswith("#") for line in lines[last:next_i]):
            raise SkillfilesError(f"frontmatter {key} must be a simple text value, not a nested value")
        _text(value, f"frontmatter {key}")
        fields[key] = value
        if key == "description":
            span = (offsets[i], offsets[last], match[1] + ":" + match[2],
                    scalar, suffix, lines[i][len(clean(lines[i])):])
    if not fields.get("name") or "description" not in fields:
        raise SkillfilesError("frontmatter must contain name and description")
    _text(fields["description"], "description", 1024, single=True)
    body = text[offsets[end + 1]:]
    _text(body, "body", 200_000)
    newline = (_NEWLINE.search(text) or ["\n"])[0]
    return fields, body, span, offsets[end + 1], newline


def _block_description(lines, start, stop):
    """Read a block for the one-line editor, leaving trailing trivia untouched."""
    indent, parts, last = None, [], start
    for i in range(start, stop):
        line = lines[i].rstrip("\r\n")
        if not line.strip():
            parts.append("")
            continue
        spaces = len(line) - len(line.lstrip(" "))
        if not spaces or (indent is not None and spaces < indent):
            break
        indent = spaces if indent is None else indent
        parts.append(line[indent:])
        last = i + 1
    return " ".join(parts).strip(), last


def _load(root, id):
    folder = root / id
    others = _tree(folder)
    text, mode = skillset._read(folder / "SKILL.md", "SKILL.md")
    parsed = _parse(text)
    return text, mode, parsed, others


def _result(id, parsed, others):
    return {"id": id, "frontmatter": parsed[0], "body": parsed[1], "files": others}


def read(id, *, skills=SKILLS):
    """Return frontmatter fields, Markdown body and relative paths of other files."""
    _id(id)
    with _operation(skills) as root:
        _, _, parsed, others = _load(root, id)
        return _result(id, parsed, others)


def _backup_folder(backups, kind):
    folder = _directory(Path(backups) / f"{date.today().isoformat()}-skill-files", create=True)
    return Path(tempfile.mkdtemp(prefix=kind + "-", dir=folder))


def _sync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def create(id, description, body, *, skills=SKILLS):
    """Create one new skill, refusing invalid or already existing ids."""
    _id(id)
    _text(description, "description", 1024, single=True)
    _text(body, "body", 200_000)
    text = f"---\nname: {id}\ndescription: {json.dumps(description, ensure_ascii=False)}\n---\n{body}"
    parsed = _parse(text)
    with _operation(skills) as root:
        folder = root / id
        if os.path.lexists(folder):
            raise SkillfilesError(f"skill {id!r} already exists")
        # Build off-catalogue, then expose a complete folder with one rename.
        staged = Path(tempfile.mkdtemp(prefix=".skill-new-", dir=root))
        try:
            with skillset._staged(staged / "SKILL.md", text, 0o644) as ready:
                os.replace(ready, staged / "SKILL.md")
            _sync(staged)
            os.rename(staged, folder)
            _sync(root)
        finally:
            if staged.exists():
                (staged / "SKILL.md").unlink(missing_ok=True)
                staged.rmdir()
        return {**_result(id, parsed, []), "changed": True}


def _description(value, old):
    if old.startswith("'"):
        return "'" + value.replace("'", "''") + "'"
    if value == value.strip(" \t") and not old.startswith(('"', ">", "|")):
        try:
            if _scalar(value) == (value, ""):
                return value
        except SkillfilesError:
            pass
    return json.dumps(value, ensure_ascii=False)


def _write_backup_kind(root, id):
    namespace = hashlib.sha256(os.fsencode(root / id)).hexdigest()
    return f"write-{id}-{namespace}"


def _previous_description(backups, kind, text, parsed, value):
    """Recover original quoting, wrapping and indentation when reverting an edit."""
    try:
        backup_root = _directory(backups)
    except FileNotFoundError:
        return None
    _, _, (start, end, _, _, suffix, eol), body_start, _ = parsed
    surrounding = (text[:start], text[end:body_start])
    for day in sorted(backup_root.glob("????-??-??-skill-files"), reverse=True):
        if not stat.S_ISDIR(day.lstat().st_mode):
            continue
        for folder in sorted(day.glob(f"{kind}-*"), key=lambda p: p.lstat().st_mtime_ns, reverse=True):
            if not stat.S_ISDIR(folder.lstat().st_mode):
                continue
            try:
                original, _ = skillset._read(folder / "SKILL.md", "backup")
                fields, _, (a, b, _, _, old_suffix, old_eol), stop, _ = _parse(original)
            except SkillfilesError:
                continue
            if (fields["description"] == value and (old_suffix, old_eol) == (suffix, eol)
                    and (original[:a], original[b:stop]) == surrounding):
                return original[a:b]
    return None


def _unchanged(path, text):
    """Refuse when SKILL.md is no longer what this write read (another process wrote it meanwhile)."""
    if skillset._read(path, "SKILL.md")[0] != text:
        raise SkillfilesError("SKILL.md changed while this edit was being made; nothing was written. Try again.")


def _prune(backup_dir, kind):
    """Keep the newest skillset.MAX_BACKUPS write backups of one skill in a day's folder."""
    mine = sorted((p for p in backup_dir.parent.glob(f"{kind}-*") if p.is_dir() and not p.is_symlink()),
                  key=lambda p: p.lstat().st_mtime_ns)
    for old in mine[:max(len(mine) - skillset.MAX_BACKUPS, 0)]:
        if [child.name for child in old.iterdir()] == ["SKILL.md"]:  # only what this module wrote
            (old / "SKILL.md").unlink()
            old.rmdir()


def write(id, body=None, description=None, *, skills=SKILLS, backups=BACKUPS):
    """Atomically update body/description, preserving all other frontmatter bytes."""
    _id(id)
    if body is not None:
        _text(body, "body", 200_000)
    if description is not None:
        _text(description, "description", 1024, single=True)
    with _operation(skills, backups=backups) as root:
        text, mode, parsed, others = _load(root, id)
        fields, old_body, span, body_start, newline = parsed
        kind = _write_backup_kind(root, id)
        updated = text
        if description is not None and description != fields["description"]:
            start, end, prefix, old, suffix, eol = span
            replacement = _previous_description(backups, kind, text, parsed, description)
            if replacement is None:
                replacement = prefix + _description(description, old) + suffix + eol
            updated = text[:start] + replacement + text[end:]
            body_start += len(replacement) - (end - start)
        if body is not None and body != old_body:
            head = updated[:body_start]
            if body and not head.endswith(("\r", "\n")):
                head += newline
            updated = head + _NEWLINE.sub(newline, body)
        new_parsed = _parse(updated)
        result = _result(id, new_parsed, others)
        if updated == text:
            return {**result, "changed": False, "message": "No change needed."}
        path = root / id / "SKILL.md"
        _unchanged(path, text)
        backup_dir = _backup_folder(backups, kind)
        backup = backup_dir / "SKILL.md"
        with skillset._staged(backup, text, mode) as staged:
            os.replace(staged, backup)
        _sync(backup_dir)
        with skillset._staged(path, updated, mode) as staged:
            _unchanged(path, text)  # again, right before the replace
            os.replace(staged, path)
        _sync(path.parent)
        _prune(backup_dir, kind)
        return {**result, "changed": True, "backup": str(backup)}


def delete(id, *, skills=SKILLS, backups=BACKUPS, config=CONFIG, force=False):
    """Move a skill to backups, checking references in every plugin preset."""
    _id(id)
    with _operation(skills, config=config, backups=backups) as root:
        _load(root, id)  # Refuse unreadable frontmatter and unsafe ancillary files.
        obj = skillset._parse(skillset._read(Path(config), "config")[0])
        agents = sorted({agent for entries in obj["presets"].values() for agent, spec in entries.items()
                         if {id, "*"}.intersection(spec.get("skills", []))})
        if agents and not force:
            raise SkillfilesError(f"skill {id!r} is still used by agents: {', '.join(agents)}; use force to delete")
        container = _backup_folder(backups, "delete-" + id)
        record = container / "record.json"
        data = {"version": 1, "id": id, "skills": str(root)}
        with skillset._staged(record, json.dumps(data) + "\n", 0o600) as staged:
            os.replace(staged, record)
        try:
            os.rename(root / id, container / id)
        except BaseException:
            record.unlink()
            container.rmdir()
            raise
        _sync(container)
        _sync(root)
        warnings = [f"Deleted skill is still listed by agents: {', '.join(agents)}. Presets were not changed."] if agents else []
        return {"id": id, "changed": True, "record": str(record), "warnings": warnings}


def restore(record, *, skills=SKILLS, backups=BACKUPS):
    """Restore a deleted folder from a backup record, never replacing an existing id."""
    with _operation(skills, backups=backups) as root:
        backup_root = _directory(backups)
        path = Path(os.path.abspath(record))
        if (path.name != "record.json" or path.parent.parent.parent != backup_root
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}-skill-files", path.parent.parent.name)
                or not path.parent.name.startswith("delete-")):
            raise SkillfilesError("undo record must be a delete record inside the backups folder")
        _directory(path.parent)
        try:
            data = json.loads(skillset._read(path, "undo record")[0], object_pairs_hook=skillset._unique)
        except ValueError as exc:
            raise SkillfilesError(f"undo record is not parseable JSON: {exc}") from exc
        if (not isinstance(data, dict) or set(data) != {"version", "id", "skills"}
                or data["version"] != 1 or data["skills"] != str(root)):
            raise SkillfilesError("undo record does not match this skills folder")
        id = data["id"]
        _id(id)
        _load(path.parent, id)
        destination = root / id
        if os.path.lexists(destination):
            raise SkillfilesError(f"skill {id!r} already exists; restore refused")
        os.rename(path.parent / id, destination)
        _sync(root)
        _sync(path.parent)
        return {"id": id, "changed": True, "record": str(path)}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -m agentgraph.skillfiles", description=__doc__,
                                     allow_abbrev=False)
    commands = parser.add_subparsers(dest="op", required=True)

    def options(target, prefix):
        for name in ("skills", "backups", "config"):
            target.add_argument("--" + name, type=Path, dest=prefix + name)
        target.add_argument("--json", action="store_true", dest=prefix + "json")

    options(parser, "root_")
    for op in ("show", "new", "write", "delete", "restore"):
        sub = commands.add_parser(op, allow_abbrev=False)
        options(sub, "")
        sub.add_argument("record" if op == "restore" else "id")
        if op in {"new", "write"}:
            sub.add_argument("--description", required=op == "new")
            sub.add_argument("--text-file", required=op == "new")
        if op == "delete":
            sub.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    output_json = args.json or args.root_json
    locations = {key: getattr(args, key) or getattr(args, "root_" + key) or default
                 for key, default in (("skills", SKILLS), ("backups", BACKUPS), ("config", CONFIG))}
    try:
        if args.op in {"new", "write"}:
            body = None
            if args.text_file is not None:
                if args.text_file == "-":
                    body = sys.stdin.read(200_001)
                    _text(body, "body", 200_000)
                else:
                    path = Path(args.text_file)
                    _directory(path.parent)
                    body = skillset._read(path, "text file")[0]
            kwargs = {"skills": locations["skills"]}
            if args.op == "write":
                kwargs["backups"] = locations["backups"]
            result = (create if args.op == "new" else write)(args.id, body=body,
                                                             description=args.description, **kwargs)
        elif args.op == "show":
            result = read(args.id, skills=locations["skills"])
        elif args.op == "delete":
            result = delete(args.id, force=args.force, **locations)
        else:
            result = restore(args.record, skills=locations["skills"], backups=locations["backups"])
    except (SkillfilesError, OSError, UnicodeError) as exc:
        if output_json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"skillfiles: {exc}", file=sys.stderr)
        return 1
    if output_json:
        print(json.dumps(result, ensure_ascii=False))
    elif args.op == "show":
        print(json.dumps(result["frontmatter"], ensure_ascii=False, indent=2))
        print(result["body"], end="")
        if result["files"]:
            print("\nOther files: " + ", ".join(result["files"]))
    else:
        print(result.get("message", f"{args.op}: {result['id']}"))
        if result.get("record"):
            print("Undo record: " + result["record"])
        if result.get("backup"):
            print("Backup: " + result["backup"])
        for warning in result.get("warnings", []):
            print("Warning: " + warning)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
