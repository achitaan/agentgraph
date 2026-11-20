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
        names = sorted(child.name for child in old.iterdir())
        if names in (["SKILL.md"], ["SKILL.md", "record.json"]):  # only what this module wrote
            for name in names:
                (old / name).unlink()
            old.rmdir()


def write(id, body=None, description=None, *, skills=SKILLS, backups=BACKUPS):
    """Atomically update body/description, preserving all other frontmatter bytes."""
    _id(id)
    if body is not None:
        _text(body, "body", 200_000)
    if description is not None:
        _text(description, "description", 1024, single=True)
    with _operation(skills, backups=backups) as root:
        _refuse_read_only(id, root)
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
        backup, record = _replace_text(root, id, text, updated, mode, kind, backups)
        return {**result, "changed": True, "backup": backup, "record": record}


def _replace_text(root, id, text, updated, mode, kind, backups):
    """Replace SKILL.md (still `text`) with `updated`, after saving `text` and an undo record (`restore` puts
    `text` back while the file is still `updated`). Returns (backup path, record path)."""
    path = root / id / "SKILL.md"
    _unchanged(path, text)
    backup_dir = _backup_folder(backups, kind)
    backup = backup_dir / "SKILL.md"
    with skillset._staged(backup, text, mode) as staged:
        os.replace(staged, backup)
    record = backup_dir / "record.json"
    data = {"version": 1, "op": "write", "id": id, "skills": str(root), "after": _digest(updated)}
    with skillset._staged(record, json.dumps(data) + "\n", 0o600) as staged:
        os.replace(staged, record)
    _sync(backup_dir)
    with skillset._staged(path, updated, mode) as staged:
        _unchanged(path, text)  # again, right before the replace
        os.replace(staged, path)
    _sync(path.parent)
    _prune(backup_dir, kind)
    return str(backup), str(record)


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def delete(id, *, skills=SKILLS, backups=BACKUPS, config=CONFIG, force=False, wildcards_hold=True,
           agents_dir=skillset.AGENTS_DIR):
    """Move a skill to backups, checking references in every plugin preset and in every agent file in
    `agents_dir` (added ones by their rules, hand-written ones when their frontmatter names the skill). An agent
    with every skill ('*') holds it too, unless `wildcards_hold` is False: then it is only named in a warning (it
    loses this one)."""
    _id(id)
    from . import agentfiles  # agentfiles imports skillset, as this module does
    with _operation(skills, config=config, backups=backups) as root:
        _refuse_read_only(id, root)
        _load(root, id)  # Refuse unreadable frontmatter and unsafe ancillary files.
        obj = skillset._parse(skillset._read(Path(config), "config")[0])
        held, every_file = agentfiles.holders(id, agents_dir) if agents_dir is not None else ([], [])
        holding = {id, "*"} if wildcards_hold else {id}
        agents = sorted({agent for entries in obj["presets"].values() for agent, spec in entries.items()
                         if holding.intersection(spec.get("skills", []))}
                        | set(held) | (set(every_file) if wildcards_hold else set()))
        every = sorted(({agent for entries in obj["presets"].values() for agent, spec in entries.items()
                         if "*" in spec.get("skills", [])} | set(every_file)) - set(agents))
        if agents and not force:
            raise SkillfilesError(f"skill {id!r} is still used by agents: {', '.join(agents)}; "
                                  + ("use force to delete" if wildcards_hold else "take it from them first"))
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
        if every and not wildcards_hold:
            warnings.append(f"{', '.join(every)} {'has' if len(every) == 1 else 'have'} every skill and will no longer "
                            f"have {id}.")
        return {"id": id, "changed": True, "record": str(record), "warnings": warnings}


def restore(record, *, skills=SKILLS, backups=BACKUPS):
    """Restore a deleted folder from a backup record, never replacing an existing id."""
    with _operation(skills, backups=backups) as root:
        backup_root = _directory(backups)
        path = Path(os.path.abspath(record))
        if (path.name != "record.json" or path.parent.parent.parent != backup_root
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}-skill-files", path.parent.parent.name)
                or not path.parent.name.startswith(("delete-", "write-"))):
            raise SkillfilesError("undo record must be a delete or write record inside the backups folder")
        _directory(path.parent)
        try:
            data = json.loads(skillset._read(path, "undo record")[0], object_pairs_hook=skillset._unique)
        except ValueError as exc:
            raise SkillfilesError(f"undo record is not parseable JSON: {exc}") from exc
        if path.parent.name.startswith("write-"):
            return _restore_write(path, data, root, backups)
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


def _restore_write(path, data, root, backups):
    """Put back the text a write replaced, while SKILL.md is still what that write left. Returns a record that
    undoes this in turn."""
    if (not isinstance(data, dict) or set(data) != {"version", "op", "id", "skills", "after"} or data["version"] != 1
            or data["op"] != "write" or data["skills"] != str(root) or not isinstance(data["after"], str)):
        raise SkillfilesError("undo record does not match this skills folder")
    id = data["id"]
    _id(id)
    kind = _write_backup_kind(root, id)
    if not path.parent.name.startswith(kind + "-"):
        raise SkillfilesError("undo record does not match this skills folder")
    _refuse_read_only(id, root)
    text, mode, _, others = _load(root, id)
    if _digest(text) != data["after"]:
        raise SkillfilesError(f"skill {id!r} was changed after that edit; nothing was restored")
    previous, _ = skillset._read(path.parent / "SKILL.md", "backup")
    parsed = _parse(previous)
    backup, record = _replace_text(root, id, text, previous, mode, kind, backups)
    return {**_result(id, parsed, others), "changed": True, "backup": backup, "record": record}


# --- what may be edited ---------------------------------------------------------------------------

# Skills kept in step with a copy owned by someone else: readable here, never written.
LOCKED = {"shopify-ruby": "shopify-ruby is kept in step with Shopify's own copy; edit it there, not here."}


def bundled_roots():
    """The folders where installed plugins keep the skills they bundle (read only)."""
    cache = Path.home() / ".cache" / "opencode"
    return sorted({*cache.glob("packages/*/node_modules/*/src/skills"), *cache.glob("npm/*/node_modules/*/src/skills")})


def _plugin_of(target, bundled):
    """The plugin a path belongs to, or "" when it is not inside one."""
    target = Path(target)
    for base in bundled:
        base = Path(os.path.abspath(base))
        if target == base or base in target.parents:
            return base.parent.parent.name
    parts = target.parts
    if "node_modules" in parts:
        index = len(parts) - 1 - parts[::-1].index("node_modules")
        return parts[index + 1] if index + 1 < len(parts) else "a plugin"
    return ""


def access(id, *, skills=SKILLS, bundled=None):
    """(editable, why) for a skill: read-only when it is bundled with a plugin, lives outside the skills folder
    (a link), or is one of LOCKED. SkillfilesError when there is no such skill."""
    _id(id)
    bundled = bundled_roots() if bundled is None else [Path(b) for b in bundled]
    root = Path(os.path.abspath(skills))
    entry = root / id
    try:
        info = entry.lstat()
    except FileNotFoundError:
        for base in bundled:
            if (Path(base) / id / "SKILL.md").is_file():
                return False, f"{id} is bundled with the {_plugin_of(Path(base) / id, bundled)} plugin; it is " \
                              "replaced when the plugin updates, so it is read-only here."
        raise SkillfilesError(f"there is no skill {id!r}") from None
    except OSError as exc:
        raise SkillfilesError(f"skill {id!r} cannot be read: {exc.strerror}") from exc
    if stat.S_ISLNK(info.st_mode):
        target = os.path.realpath(entry)
        plugin = _plugin_of(target, bundled)
        if plugin:
            return False, f"{id} is bundled with the {plugin} plugin; it is replaced when the plugin updates, " \
                          "so it is read-only here."
        return False, f"{id} lives outside the skills folder (a symlink to {target}); edit it where it lives."
    if not stat.S_ISDIR(info.st_mode):
        return False, f"{id} is not a folder in the skills folder."
    if id in LOCKED:
        return False, LOCKED[id]
    try:
        inner = (entry / "SKILL.md").lstat()
    except FileNotFoundError:
        return False, f"{id} has no SKILL.md."
    if stat.S_ISLNK(inner.st_mode):
        return False, f"{id}'s SKILL.md lives outside the skills folder (a symlink); edit it where it lives."
    return True, ""


def _refuse_read_only(id, root):
    editable, why = access(id, skills=root, bundled=())
    if not editable:
        raise SkillfilesError(why)


def show(id, *, skills=SKILLS, bundled=None):
    """{"id", "description", "text" (the body without frontmatter), "editable", "why" when not}. A read-only
    skill is read where it lives."""
    editable, why = access(id, skills=skills, bundled=bundled)
    if editable:
        result = read(id, skills=skills)
        description, text = result["frontmatter"]["description"], result["body"]
    else:
        entry = Path(os.path.abspath(skills)) / id
        if not os.path.lexists(entry):
            bundled = bundled_roots() if bundled is None else bundled
            entry = next(Path(base) / id for base in bundled if (Path(base) / id / "SKILL.md").is_file())
        raw, _ = skillset._read(Path(os.path.realpath(entry)) / "SKILL.md", "SKILL.md")
        try:
            fields, text = _parse(raw)[:2]
            description = fields["description"]
        except SkillfilesError:
            description, text = "", raw
    return {"id": id, "description": description, "text": text, "editable": editable, **({"why": why} if why else {})}


def annotate(document, *, skills=SKILLS, bundled=None):
    """Add `editable` (and `why` when false) to each installed skill in a snapshot document. Never raises."""
    try:
        bundled = bundled_roots() if bundled is None else bundled
    except OSError:
        bundled = []
    for row in document.get("skills") or []:
        try:
            editable, why = access(row.get("name"), skills=skills, bundled=bundled)
        except (SkillfilesError, OSError, TypeError) as exc:
            editable, why = False, str(exc)
        row["editable"] = editable
        if why:
            row["why"] = why
    return document


def create_skill(id, description, body, assign=None, *, skills=SKILLS, config=CONFIG, map_path=skillset.MAP,
                 backups=BACKUPS, profiles=skillset.PROFILES, agents_dir=skillset.AGENTS_DIR):
    """Create a skill and, with `assign`, give it to that agent (as `skillset.add` does; an added agent's file in
    `agents_dir`) in the same step: if the agent cannot have it, the new skill is taken away again and nothing is
    left."""
    if assign is not None:
        skillset._agent_name(assign)
        shown = skillset.show(assign, config=config, agents_dir=agents_dir)
        if not shown["editable"]:
            raise SkillfilesError(shown.get("reason") or f"{assign}'s skills cannot be set here")
    result = create(id, description, body, skills=skills)
    if assign is None:
        return result
    try:
        given = skillset.add(assign, id, config=config, map_path=map_path, backups=backups, profiles=profiles,
                             skills_dir=skills, agents_dir=agents_dir)
    except BaseException:
        _discard(id, skills, result)
        raise
    return {**result, "assigned": {"agent": given["agent"], "skills": given["skills"]},
            "warnings": given.get("warnings", []), "message": given.get("message", "")}


def _discard(id, skills, created):
    """Take away a skill `create` just made, only while it is still exactly that."""
    with _operation(skills) as root:
        folder = root / id
        try:
            if sorted(child.name for child in folder.iterdir()) != ["SKILL.md"]:
                return
            fields, body = _parse(skillset._read(folder / "SKILL.md", "SKILL.md")[0])[:2]
        except (OSError, SkillfilesError):
            return
        if (fields.get("description"), body) == (created["frontmatter"]["description"], created["body"]):
            (folder / "SKILL.md").unlink()
            folder.rmdir()
            _sync(root)


# --- the `skill` command --------------------------------------------------------------------------


def _read_body(source):
    if source == "-":
        body = sys.stdin.read(200_001)
        _text(body, "text", 200_000)
        return body
    path = Path(source)
    _directory(path.parent)
    return skillset._read(path, "text file")[0]


def skill_main(argv=None):
    """`agentgraph skill show|create|write|delete|restore`: exits 0 on success, 1 when refused, 2 on a usage error."""
    parser = argparse.ArgumentParser(prog="agentgraph skill", description="Read or edit a skill's text.",
                                     allow_abbrev=False)
    commands = parser.add_subparsers(dest="op", required=True)

    def options(target, prefix):
        for name, help in (("skills", "the skills folder"), ("backups", "where backups and undo records go"),
                           ("config", "the plugin's preset file"), ("map", "SKILLS.md"), ("profiles", "task profiles"),
                           ("agents", "OpenCode's agents folder")):
            target.add_argument("--" + name, type=Path, dest=prefix + name, help=help)
        target.add_argument("--json", action="store_true", dest=prefix + "json")

    options(parser, "root_")
    for op in ("show", "create", "write", "delete", "restore"):
        sub = commands.add_parser(op, allow_abbrev=False)
        options(sub, "")
        sub.add_argument("record" if op == "restore" else "id")
        if op in {"create", "write"}:
            sub.add_argument("--description", required=op == "create")
            sub.add_argument("--text-file", required=True, help="a file, or - for stdin")
        if op == "create":
            sub.add_argument("--assign", metavar="AGENT", help="also give the new skill to this agent")
    args = parser.parse_args(argv)
    pick = lambda key, default: getattr(args, key) or getattr(args, "root_" + key) or default
    as_json = args.json or args.root_json
    where = {"skills": pick("skills", SKILLS), "backups": pick("backups", BACKUPS)}
    try:
        if args.op == "show":
            result = show(args.id, skills=where["skills"])
        elif args.op == "create":
            created = create_skill(args.id, args.description, _read_body(args.text_file), args.assign,
                                   skills=where["skills"], config=pick("config", CONFIG),
                                   map_path=pick("map", skillset.MAP), backups=where["backups"],
                                   profiles=pick("profiles", skillset.PROFILES),
                                   agents_dir=pick("agents", skillset.AGENTS_DIR))
            result = {"id": args.id, "description": created["frontmatter"]["description"], "text": created["body"],
                      "changed": True, **{key: created[key] for key in ("assigned", "warnings", "message") if key in created}}
        elif args.op == "write":
            written = write(args.id, body=_read_body(args.text_file), description=args.description, **where)
            result = {"id": args.id, "description": written["frontmatter"]["description"], "text": written["body"],
                      "changed": written["changed"],
                      **{key: written[key] for key in ("record", "message") if key in written}}
        elif args.op == "delete":
            result = delete(args.id, config=pick("config", CONFIG), wildcards_hold=False,
                            agents_dir=pick("agents", skillset.AGENTS_DIR), **where)
        else:
            restored = restore(str(args.record), **where)
            result = {"id": restored["id"], "changed": True, **({"record": restored["record"]}
                                                                if "frontmatter" in restored else {})}
    except (SkillfilesError, OSError, UnicodeError) as exc:
        if as_json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"agentgraph skill: {exc}", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(result, ensure_ascii=False))
    elif args.op == "show":
        print(f"{result['id']}: {result['description']}")
        if not result["editable"]:
            print(f"(read-only: {result['why']})")
        print(result["text"], end="" if result["text"].endswith("\n") else "\n")
    else:
        print(f"{args.op}: {result['id']}" + ("" if result.get("changed", True) else " (no change needed)"))
        if result.get("assigned"):
            print(f"Given to {result['assigned']['agent']}. {result.get('message', '')}".rstrip())
        if result.get("record"):
            print("Undo record: " + result["record"])
        for warning in result.get("warnings", []):
            print("Warning: " + warning)
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["skill"]:
        return skill_main(argv[1:])
    parser = argparse.ArgumentParser(prog="python3 -m agentgraph.skillfiles", description=__doc__,
                                     allow_abbrev=False)
    commands = parser.add_subparsers(dest="op", required=True)

    def options(target, prefix):
        for name in ("skills", "backups", "config", "agents"):
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
            result = delete(args.id, force=args.force,
                            agents_dir=args.agents or args.root_agents or skillset.AGENTS_DIR, **locations)
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
