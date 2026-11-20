"""OpenCode agent files this toolkit adds: `NAME.md` in the agents folder, live as soon as it is written.

An added agent is one Markdown file with YAML frontmatter, in the shape OpenCode 2.3.2 reads (`Config.AgentEncoded`
in the host's own OpenAPI document, `/openapi.json` of `opencode serve`): `description`, `mode: subagent`, `model`
("provider/model#variant") and `permissions`, an ordered list of {action, resource, effect} rules where the last
rule that matches wins. The body is the agent's instruction (the host reads it as `system`). The host watches the
folder: a new, changed or deleted file applies without a restart (see the n84 proof).

Every file this module writes carries the marker comment `# agentgraph: added` as the first frontmatter line, so
an added agent can be told from the user's own hand-written agent files: those are listed as agents but are never
written here. The permissions always start with a deny of everything and end with a deny of `subagent` (starting
other agents); in between come the read tools, the tools the agent may use, and one `skill` allow per skill.
A marked file stays the toolkit's after a hand edit: its text, its skills and its removal keep every line the
toolkit did not write, and when the writer cannot keep a hand edit exactly, all three refuse with one reason.

Writes refuse when the file changed underneath, never follow a symlink, and go by rename inside the folder, under
an flock of the folder. Undo records go to `<backups>/<date>-agent-files/`.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import ctypes
import ctypes.util
from datetime import date
import fcntl
import functools
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import unicodedata

from . import names, skillset, sources

AGENTS_DIR = sources.CONFIG_DIR / "agents"
BACKUPS = skillset.BACKUPS
LIVE = ("OpenCode watches its agents folder; the change applies at once, without a restart, from the next "
        "session or turn.")
MARK = "# agentgraph: added"
_MARK_LINE = MARK + " (written by `agentgraph agent add`; change it with `agentgraph agent text` and `agentgraph skills`)"
FILE_LIMIT = 64 * 1024
MAX_DESCRIPTION, MAX_TEXT = 300, 8000
NAME = names.AGENT  # agentgraph.names: the one definition of an agent name
ACTION = re.compile(r"[a-z*][a-z0-9_*-]{0,63}")
MODEL = re.compile(r"[^/#\s\"\\]+/[^#\s\"\\]+(?:#[^#\s\"\\]+)?")  # Config.AgentEncoded's model pattern, no spaces
EFFECTS = ("allow", "ask", "deny")
HAND_WRITTEN = ("{name} is an agent file written by hand, not by this toolkit; it is read-only here. Edit or delete "
                "it in the agents folder.")
# One reason for every refusal of a marked file the writer cannot keep exactly (text, skills and remove alike).
HAND_CHANGED = ("{name} was changed by hand in a way this toolkit cannot keep exactly; it is read-only here. Edit or "
                "delete it in the agents folder.")
VERIFIER_TEXT = ("You are given a goal and the work done for it. Check the work against the goal and report pass or "
                 "fail, with the evidence for each.")
VERIFIER_DESCRIPTION = "Checks work against a goal and reports pass or fail, with the evidence for each."
# A blank agent's description until the user writes one (its instruction starts empty; see roster.add_agent).
BLANK_DESCRIPTION = "An agent you added. Open it to say what it does."
TEMPLATES = ("blank", "verifier")

# How OpenCode 2.3.2 resolves an agent's rules, as a scratch host answered `POST /api/session/{id}/permission`
# (the n99 proof): the host puts HOST_HEAD before an agent file's rules and HOST_TAIL after them, and the last rule
# whose action and resource both match (`*` any text, `?` one character, a trailing " *" also matching nothing)
# decides; no match is a deny.
HOST_HEAD = ({"action": "*", "resource": "*", "effect": "allow"}, {"action": "read", "resource": "*.env", "effect": "ask"},
             {"action": "read", "resource": "*.env.*", "effect": "ask"},
             {"action": "read", "resource": "*.env.example", "effect": "allow"})
HOST_TAIL = ({"action": "browser", "resource": "*", "effect": "deny"},
             {"action": "skill", "resource": "browser", "effect": "deny"})

# A plugin preset entry's `permission` (OpenCode 1 keys) becomes rules in the plugin (oh-my-opencode-slim 3.0.6,
# adaptPermissions): its own first rules (`* * allow`, `external_directory * ask`, the .env reads), then one rule
# per key and pattern in the entry's order (`bash` is `shell`, `task` is `subagent`), then denies of its own tools
# and of every skill, then the agent's skills. A copy keeps that order. Its allows are limited to the host's own
# tools below: anything else (an MCP tool, a plugin tool the plugin denies after the entry's rules) is not copied
# as an allow. The plugin keeps `write`, `apply_patch` and the like as actions of their own; a deny of one of them
# also denies `edit`, an allow never allows `edit`.
READ_TOOLS = ("read", "grep", "glob", "list")
# `external_directory` is not among them: the plugin may deny it after the entry's rules (an MCP server named
# `external` becomes `external_*` deny).
COPYABLE = (*READ_TOOLS, "edit", "write", "apply_patch", "patch", "multiedit", "shell", "webfetch", "websearch",
            "codesearch", "todowrite", "todoread", "lsp")
# The actions that write files: an allow on any of them makes an agent a writer (`read_only`).
WRITE_ACTIONS = ("edit", "write", "apply_patch", "patch", "multiedit")
EDIT_KEYS = ("write", "apply_patch", "patch", "multiedit", "ast_grep_replace")
# The plugin's first rules after its `* * allow`, which a copy stands in for with the read tools.
PLUGIN_READS = ({"action": "read", "resource": "*.env", "effect": "ask"},
                {"action": "read", "resource": "*.env.*", "effect": "ask"},
                {"action": "read", "resource": "*.env.example", "effect": "allow"})
FIRST = {"action": "*", "resource": "*", "effect": "deny"}
LAST = {"action": "subagent", "resource": "*", "effect": "deny"}
_BIDI = set("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


class AgentfilesError(skillset.SkillsetError):
    pass


# No write from a stale view (contract n8 r4, stale-contract.md): every thing a view can change has a `rev`, a short
# hash of the bytes it is stored as; a write given `expect` refuses (StaleError, status 409) when it is not the
# current `rev`. `rev`, `StaleError` and `check_expect` are shared by every writer (flows, groups, skills too).
STALE = "changed since you opened it; reload and try again"


# What a rev looks like (rev's own output). An `expect` that is given must look like one on every surface (the
# endpoints answer 400, the commands and the Desktop plugin refuse it plainly); anything else is never compared.
REV = re.compile(r"[0-9a-f]{16}")
EXPECT_TEXT = "expect must be text in the form of a rev, 16 lowercase hex digits: the rev the view was showing"


def expect_ok(expect):
    """Whether `expect` is None (no check) or looks like a rev."""
    return expect is None or (isinstance(expect, str) and REV.fullmatch(expect) is not None)


class StaleError(AgentfilesError):
    """A write from a view that has not seen a later change: nothing was written; `rev` is the current one."""

    def __init__(self, what, rev):
        super().__init__(f"{what} {STALE}")
        self.rev = rev


def rev(*parts):
    """The `rev` of a thing stored as these parts (bytes or text; None for a part that is absent): 16 hex digits of
    a SHA-256 over the parts, each length-prefixed, so a part moving to the next one changes it."""
    digest = hashlib.sha256()
    for part in parts:
        if part is None:
            digest.update(b"-")
            continue
        data = part.encode("utf-8") if isinstance(part, str) else bytes(part)
        digest.update(b"+%d:" % len(data))
        digest.update(data)
    return digest.hexdigest()[:16]


def check_expect(expect, current, what):
    """Refuse (StaleError) a write given `expect` when it is not `current`; no check when `expect` is None. An
    `expect` that is given must look like a rev (expect_ok; anything else is bad input, never compared)."""
    if expect is None:
        return
    if not expect_ok(expect):
        raise AgentfilesError(EXPECT_TEXT)
    if expect != current:
        raise StaleError(what, current)


# --- the file format ------------------------------------------------------------------------------


def _quote(value):
    return json.dumps(value, ensure_ascii=True)


def _clean_text(value, kind, limit, *, single):
    if not isinstance(value, str):
        raise AgentfilesError(f"{kind} must be text")
    value = value.replace("\r\n", "\n")
    for c in value:
        if c not in "\n\t" and (unicodedata.category(c) in ("Cc", "Cs", "Zl", "Zp") or c in _BIDI):
            raise AgentfilesError(f"{kind} holds control characters (U+{ord(c):04X})")
    if single and "\n" in value.strip():
        raise AgentfilesError(f"{kind} must be one line")
    value = value.strip() if single else value.strip("\n")
    if not value.strip():
        raise AgentfilesError(f"{kind} is required")
    if len(value) > limit:
        raise AgentfilesError(f"{kind} is longer than {limit} characters")
    return value


def _rule(rule):
    if (not isinstance(rule, dict) or set(rule) != {"action", "resource", "effect"}
            or not isinstance(rule["action"], str) or not ACTION.fullmatch(rule["action"])
            or not isinstance(rule["resource"], str) or not rule["resource"] or len(rule["resource"]) > 200
            or any(ord(c) < 32 for c in rule["resource"]) or rule["effect"] not in EFFECTS):
        raise AgentfilesError(f"not a permission rule this tool writes: {rule!r}")
    return {"action": rule["action"], "resource": rule["resource"], "effect": rule["effect"]}


def narrow(rules):
    """The rules an added agent may have, in their order: deny everything first, no allow (or ask) for a wildcard
    action, `subagent` denied last; exact repeats dropped (keeping the last, which is the one that counts). A deny
    of everything later in the list stays where it is: it overrides every rule before it."""
    kept = []
    for rule in map(_rule, rules):
        if rule["action"] == "subagent" or ("*" in rule["action"] and rule["effect"] != "deny"):
            continue
        kept.append(rule)
    out = [rule for index, rule in enumerate(kept) if rule not in kept[index + 1:]]
    while out and out[0] == FIRST:  # the leading deny of everything is written once, below
        out.pop(0)
    return [dict(FIRST), *out, dict(LAST)]


@functools.lru_cache(maxsize=16384)
def matches(pattern, text):
    """Whether a rule's action or resource pattern matches, as OpenCode matches them (the n99 proof): `*` any
    text, `?` one character, a trailing " *" also matching nothing ("git *" matches "git")."""
    expression = re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".")
    for space in (r"\ .*", " .*"):
        if expression.endswith(space):
            expression = expression[:-len(space)] + "( .*)?"
            break
    return re.fullmatch(expression, text, re.S) is not None


def resolve(rules, action, resource):
    """The effect of the last rule in `rules` that matches, or "deny" when none does."""
    for rule in reversed(rules):
        if matches(rule["action"], action) and matches(rule["resource"], resource):
            return rule["effect"]
    return "deny"


def host_effect(permissions, action, resource):
    """What OpenCode lets an agent file with these rules do: HOST_HEAD, the file's rules and HOST_TAIL, resolved."""
    return resolve([*HOST_HEAD, *permissions, *HOST_TAIL], action, resource)


def _rule_lines(rules):
    lines = []
    for rule in narrow(rules):
        lines += [f"  - action: {_quote(rule['action'])}", f"    resource: {_quote(rule['resource'])}",
                  f"    effect: {rule['effect']}"]
    return lines


def _encoded(head, body):
    data = ("---\n" + "\n".join(head) + "\n---\n" + body).encode("utf-8")
    if len(data) > FILE_LIMIT:
        raise AgentfilesError("the agent file would be too large")
    return data


def render(description, model, permissions, text):
    """The bytes of an added agent's file."""
    head = [_MARK_LINE, f"description: {_quote(description)}", "mode: subagent", f"model: {_quote(model)}",
            "permissions:", *_rule_lines(permissions)]
    return _encoded(head, text.rstrip("\n") + "\n")


_QUOTED = r'("(?:[^"\\\n]|\\.)*")'
_FIELDS = {"description": re.compile(r"description: " + _QUOTED), "mode": re.compile(r"mode: subagent"),
           "model": re.compile(r"model: " + _QUOTED), "permissions": re.compile(r"permissions:")}
_OWNED = re.compile(r"[\"']?(description|mode|model|permissions)[\"']?[ \t]*:")
# Keys that give an agent rights too: OpenCode 2.3.2 still reads OpenCode 1's `permission` and `tools` maps in an
# agent file (the n99 proof), and a YAML merge key could bring in any key. A hand-added one is a hand change the
# writer cannot keep: the file is read-only here, and what the view says it may do would not be the whole story.
_RIGHTS = re.compile(r"[\"']?(permission|tools|<<)[\"']?[ \t]*:")
# Every other top-level frontmatter line must be a plain `key: value` (or `key:`): OpenCode's YAML reader also
# takes `? permission`, `!!str permission:` and `"permissio\u006e":` as the key `permission` (the n99 proof), so
# any other shape is a hand change the writer cannot keep. A hand key's indented or `- ` lines, a comment and a
# blank line are kept.
_PLAIN = re.compile(r"[a-z_][a-z0-9_]*:( |$)")
_RULE_LINE = (re.compile(r'  - action: ("[^"\n]*")'), re.compile(r"    resource: " + _QUOTED),
              re.compile(r"    effect: (allow|ask|deny)"))


def is_added(data):
    """Whether file bytes carry this toolkit's marker (as the first frontmatter line)."""
    return data.startswith(b"---\n" + MARK.encode("ascii"))


def parse(data):
    """{"description", "model", "permissions", "text", "head", "spans"} of a marked file the writer can keep
    exactly, or None (a hand-written file, or a hand change the writer cannot keep).

    The toolkit's own lines are the marker, `description`, `mode: subagent`, `model`, the `permissions` block (in
    the order and shape `with_skills` writes) and the text after the frontmatter. Any other frontmatter line (a
    hand-added `temperature: 0.2`, say) is kept byte for byte by every change (`rebuild`). The writer cannot keep a
    file whose own lines repeat or are not in its shape, whose `permissions` block continues in lines it does not
    write, or that gives rights another way (a hand-added `permission` or `tools` map): such a file is read-only
    here, with the same reason for every change (HAND_CHANGED)."""
    try:
        text = data.decode("utf-8")
    except (UnicodeDecodeError, AttributeError):
        return None
    if "\r" in text or not text.startswith("---\n" + MARK):
        return None
    lines = text.split("\n")
    try:
        close = lines.index("---", 1)
    except ValueError:
        return None
    if close == len(lines) - 1:  # no newline after the closing line
        return None
    head, body = lines[1:close], "\n".join(lines[close + 1:])
    fields, spans, index = {}, {}, 1
    try:
        hand = False  # whether the last top-level line was a hand-added key (its own lines may follow)
        while index < len(head):
            line = head[index]
            if _RIGHTS.match(line):
                return None
            key = _OWNED.match(line)
            if not key:
                if line.strip() == "" or line.startswith("#"):
                    pass
                elif line.startswith((" ", "- ")) or line == "-":
                    if not hand:
                        return None
                elif _PLAIN.match(line):
                    hand = True
                else:
                    return None
                index += 1
                continue
            hand = False
            key = key.group(1)
            found = _FIELDS[key].fullmatch(head[index])
            if key in spans or not found:
                return None
            if key in ("description", "model"):
                fields[key] = json.loads(found.group(1))
            if key != "permissions":
                spans[key] = index
                index += 1
                continue
            start = index = index + 1
            rules = []
            while index + 3 <= len(head):
                parts = [pattern.fullmatch(head[index + offset]) for offset, pattern in enumerate(_RULE_LINE)]
                if not all(parts):
                    break
                rules.append({"action": json.loads(parts[0].group(1)), "resource": json.loads(parts[1].group(1)),
                              "effect": parts[2].group(1)})
                index += 3
            if index < len(head) and head[index][:1] in (" ", "\t", "-"):
                return None  # the block goes on in lines the writer does not write
            spans[key] = (start, index)
            fields[key] = rules
        if set(spans) != set(_FIELDS) or with_skills(fields["permissions"], skills_of(fields["permissions"])) != \
                fields["permissions"]:
            return None
    except (ValueError, AgentfilesError):
        return None
    return {**fields, "text": body, "head": head, "spans": spans}


def rebuild(parsed, *, permissions=None, text=None):
    """The bytes of a parsed file with its `permissions` block and/or text replaced; every other line kept."""
    head = list(parsed["head"])
    if permissions is not None:
        start, end = parsed["spans"]["permissions"]
        head[start:end] = _rule_lines(permissions)
    return _encoded(head, parsed["text"] if text is None else text.rstrip("\n") + "\n")


def skills_of(permissions):
    """The skills a rule list allows, in order ("*" for every skill)."""
    allowed = []
    for rule in permissions:
        if rule["action"] == "skill":
            if rule["effect"] == "allow" and rule["resource"] not in allowed:
                allowed.append(rule["resource"])
            elif rule["effect"] != "allow" and rule["resource"] in allowed:
                allowed.remove(rule["resource"])
    return allowed


def with_skills(permissions, skills):
    """The rules with the `skill` allows replaced by one per skill (after the others, before `subagent`)."""
    rest = [rule for rule in permissions if rule["action"] != "skill"]
    return narrow([*rest, *({"action": "skill", "resource": skill, "effect": "allow"} for skill in skills)])


def _allows_some(permissions, action):
    """Whether some resource of `action` is allowed outright: a rule allows it that no later deny (or ask) of the
    action on every resource overrides."""
    for rule in reversed(permissions):
        if not matches(rule["action"], action):
            continue
        if rule["effect"] == "allow":
            return True
        if set(rule["resource"]) == {"*"}:
            return False  # every earlier rule is overridden for this action
    return True  # nothing matched: the host's own first rule allows everything


def read_only(permissions):
    """Whether the agent may write no file outright: none of WRITE_ACTIONS (`edit`, `write`, `apply_patch`, `patch`,
    `multiedit`) is allowed on any resource. (`host_effect` decides one action and resource; this is every resource
    at once.)"""
    return not any(_allows_some(permissions, action) for action in WRITE_ACTIONS)


def _note(notes, text):
    if text not in notes:
        notes.append(text)


def _copied(key, pattern, effect, notes):
    """The rules one key and pattern of a plugin permission become in a copy (see COPYABLE)."""
    if key == "task":
        if effect != "deny":
            _note(notes, "the new agent may not start other agents: subagent is denied")
        return []
    if key == "skill":
        return []  # the plugin denies every skill after the entry's rules, then allows the agent's skills
    if key == "*":
        reads = [{"action": tool, "resource": pattern, "effect": effect} for tool in READ_TOOLS]
        if effect == "deny":
            return [{"action": "*", "resource": pattern, "effect": "deny"}]
        _note(notes, "the new agent gets no wildcard allow: the '*' rule only lets it read (read, grep, glob, list)")
        if effect == "ask":  # every action asks from here on in the source: in the copy, all but reading is denied
            return [{"action": "*", "resource": pattern, "effect": "deny"}, *reads]
        return reads
    action = "shell" if key == "bash" else key
    out = []
    if effect == "deny" or action in COPYABLE:
        out.append({"action": action, "resource": pattern, "effect": effect})
    elif effect == "ask":  # an ask may override an earlier allow in the source: the copy denies instead
        out.append({"action": action, "resource": pattern, "effect": "deny"})
        _note(notes, f"the new agent gets no ask for {key!r}: it is denied (only OpenCode's own tools are copied "
                     "as allowed or asked)")
    else:
        _note(notes, f"the new agent gets no {effect} for {key!r}: only OpenCode's own tools are copied as allowed")
    if key in EDIT_KEYS and effect == "deny":
        out.append({"action": "edit", "resource": pattern, "effect": "deny"})
    return out


def from_plugin(permission, skills=()):
    """Rules from a plugin preset entry's `permission` (OpenCode 1 keys) and `skills`, in the order the plugin
    gives them (see COPYABLE); never wider than the source under the host's last-match-wins: the plugin's first
    `* * allow` and any '*' allow only let the copy read, a later deny stays where it is, `task` is denied.
    No permission at all (the host default, as impl-claude) becomes the writer shape. Returns (rules, notes)."""
    notes = []
    rules = [*({"action": tool, "resource": "*", "effect": "allow"} for tool in READ_TOOLS), *PLUGIN_READS]
    if permission is None:
        permission = {"bash": "allow", "edit": "allow"}
    if isinstance(permission, str) and permission in EFFECTS:
        permission = {"*": permission}  # as the plugin reads a permission given as one word
    if not isinstance(permission, dict):
        raise AgentfilesError("the permission to copy is not an object")
    for key, value in permission.items():
        if not isinstance(key, str) or not ACTION.fullmatch(key):
            raise AgentfilesError(f"the permission {key!r} is not one this tool can copy")
        if isinstance(value, str) and value in EFFECTS:
            patterns = [("*", value)]
        elif isinstance(value, dict) and value and all(isinstance(k, str) and k and v in EFFECTS for k, v in value.items()):
            patterns = list(value.items())  # in the source's order: the last that matches wins
        else:
            raise AgentfilesError(f"the permission for {key!r} is not one this tool can copy")
        for pattern, effect in patterns:
            rules += _copied(key, pattern, effect, notes)
    if "*" in skills:
        rules.append({"action": "skill", "resource": "*", "effect": "allow"})
    else:
        rules += [{"action": "skill", "resource": skill, "effect": "allow"} for skill in skills]
    return narrow(rules), notes


WRITER = from_plugin(None)[0]


# --- the folder -------------------------------------------------------------------------------------


@contextmanager
def _folder(agents_dir, *, create=False):
    """The agents folder, opened without following a symlink and locked for one writer at a time."""
    path = Path(os.path.abspath(agents_dir))
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        if not create:
            raise AgentfilesError(f"there is no agents folder at {path}") from None
        try:
            os.mkdir(path, 0o755)
        except OSError as exc:
            raise AgentfilesError(f"cannot make the agents folder {path}: {exc.strerror}") from exc
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise AgentfilesError(f"the agents folder {path} is not a plain folder ({exc.strerror})") from exc
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd
    finally:
        os.close(fd)


def _read_at(fd, name):
    """The bytes of a regular file in the folder (never through a symlink), or None when there is none."""
    try:
        file = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise AgentfilesError(f"{name} is not a regular file ({exc.strerror})") from exc
    with os.fdopen(file, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise AgentfilesError(f"{name} is not a regular file")
        data = stream.read(FILE_LIMIT + 1)
    if len(data) > FILE_LIMIT:
        raise AgentfilesError(f"{name} is larger than an agent file this tool writes")
    return data


def _write_at(fd, name, data, before):
    """Write `data` as `name` by rename inside the folder, refusing when the file is no longer `before` (None:
    there must be no such file). `data` None deletes it."""
    if _read_at(fd, name) != before:
        raise AgentfilesError(f"{name} changed while this change was being made; nothing was written. Try again.")
    if data is None:
        os.unlink(name, dir_fd=fd)
        os.fsync(fd)
        return
    temp = f".{name}.{secrets.token_hex(6)}.tmp"
    file = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=fd)
    try:
        with os.fdopen(file, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if _read_at(fd, name) != before:  # again, right before the rename
            raise AgentfilesError(f"{name} changed while this change was being made; nothing was written. Try again.")
        if before is None:
            _rename_new(fd, temp, name)  # never over a file that appeared
        else:
            os.rename(temp, name, src_dir_fd=fd, dst_dir_fd=fd)
    except FileExistsError:
        raise AgentfilesError(f"{name} appeared while this change was being made; nothing was written.") from None
    finally:
        try:
            os.unlink(temp, dir_fd=fd)
        except FileNotFoundError:
            pass
    os.fsync(fd)


_LIBC = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)


def _rename_new(fd, source, target):
    """Rename inside the folder, refused (FileExistsError) when `target` exists. A new agent file is never a hard
    link: OpenCode 2.3.2 on macOS did not see the later deletion of a file made with link() (the n84 proof)."""
    exclusive = getattr(_LIBC, "renameatx_np", None)  # macOS: RENAME_EXCL
    flag = 0x4
    if exclusive is None:
        exclusive, flag = getattr(_LIBC, "renameat2", None), 0x1  # Linux: RENAME_NOREPLACE
    if exclusive is None:
        if _read_at(fd, target) is not None:  # the folder's flock keeps out this toolkit's other writers
            raise FileExistsError(target)
        os.rename(source, target, src_dir_fd=fd, dst_dir_fd=fd)
        return
    if exclusive(fd, os.fsencode(source), fd, os.fsencode(target), flag) != 0:
        error = ctypes.get_errno()
        if error == 17:  # EEXIST
            raise FileExistsError(target)
        raise OSError(error, os.strerror(error), target)


def _taken(fd, name):
    """The file names in the folder that already hold this agent: `NAME.md`, `NAME.md.disabled` and the like."""
    return sorted(entry for entry in os.listdir(fd) if entry == name + ".md" or entry.startswith(name + ".md."))


def check_name(name):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise AgentfilesError("invalid agent name: use a lowercase letter, then lowercase letters, digits, '_' or '-' "
                              "(64 at most)")
    return name


# --- reading -----------------------------------------------------------------------------------------


def listing(agents_dir=AGENTS_DIR):
    """{name: {"added": bool, "editable": bool, "why": str}} for each `*.md` in the agents folder. Never raises."""
    found = {}
    try:
        fd = os.open(Path(agents_dir), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return found
    try:
        for entry in sorted(os.listdir(fd))[:500]:
            if not entry.endswith(".md") or entry.startswith("."):
                continue
            name = entry[:-3]
            try:
                data = _read_at(fd, entry)
            except AgentfilesError:
                data = None
            if data is not None and is_added(data):
                parsed = parse(data)
                found[name] = {"added": True, "editable": parsed is not None, "parsed": parsed,
                               "why": "" if parsed else HAND_CHANGED.format(name=f"{name}.md")}
            else:
                found[name] = {"added": False, "editable": False, "parsed": None,
                               "why": HAND_WRITTEN.format(name=f"{name}.md")}
    finally:
        os.close(fd)
    return found


def _load(fd, name):
    """(bytes, parsed) of an added agent's file; refused for a missing, hand-written or hand-changed file."""
    data = _read_at(fd, name + ".md")
    if data is None:
        raise AgentfilesError(f"there is no agent file {name}.md")
    if not is_added(data):
        raise AgentfilesError(HAND_WRITTEN.format(name=f"{name}.md"))
    parsed = parse(data)
    if parsed is None:
        raise AgentfilesError(HAND_CHANGED.format(name=f"{name}.md"))
    return data, parsed


def read(name, *, agents_dir=AGENTS_DIR):
    """{"name", "description", "model", "text", "skills", "read_only"} of an added agent."""
    check_name(name)
    with _folder(agents_dir) as fd:
        _, parsed = _load(fd, name)
    return _shown(name, parsed)


def _shown(name, parsed):
    return {"name": name, "description": parsed["description"], "model": parsed["model"],
            "text": parsed["text"].rstrip("\n"), "skills": skills_of(parsed["permissions"]),
            "read_only": read_only(parsed["permissions"]), "permissions": summary(parsed["permissions"])}


def summary(permissions):
    """What an added agent may do, from its rules (the last rule that matches wins): `default` (the first rule:
    always deny), `rules` (the tool rules after it, in order, without the skill rules; a later deny of everything
    is one of them), `skills`, `start_agents` (the last rule: always deny) and `read_only`. No rule allows every
    action."""
    start = 1 if permissions and permissions[0] == FIRST else 0
    end = len(permissions) - 1 if len(permissions) > start and permissions[-1] == LAST else len(permissions)
    middle = [dict(rule) for rule in permissions[start:end] if rule["action"] != "skill"]
    return {"default": permissions[0]["effect"] if permissions and permissions[0] == FIRST else "allow",
            "rules": middle, "skills": skills_of(permissions),
            "start_agents": permissions[-1]["effect"] if permissions and permissions[-1] == LAST else "allow",
            "read_only": read_only(permissions)}


def holders(skill, agents_dir=AGENTS_DIR):
    """(agents whose file holds `skill`, agents whose file allows every skill), by name, sorted. An added file the
    writer can keep holds the skills its rules allow; any other agent file (hand-written, or changed by hand)
    holds a skill when its frontmatter names it. Never raises."""
    held, every = [], []
    word = re.compile(r"(?<![A-Za-z0-9_.-])" + re.escape(skill) + r"(?![A-Za-z0-9_.-])")
    try:
        fd = os.open(Path(agents_dir), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return held, every
    try:
        for entry in sorted(os.listdir(fd))[:500]:
            if not entry.endswith(".md") or entry.startswith("."):
                continue
            try:
                file = os.open(entry, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            except OSError:
                continue
            with os.fdopen(file, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    continue
                data = stream.read(1024 * 1024)
            parsed = parse(data) if is_added(data) else None
            if parsed is not None:
                allowed = skills_of(parsed["permissions"])
                if skill in allowed:
                    held.append(entry[:-3])
                elif "*" in allowed:
                    every.append(entry[:-3])
                continue
            text = data.decode("utf-8", "replace").replace("\r\n", "\n")
            if not text.startswith("---\n"):
                continue
            end = re.search(r"^---[ \t]*$", text[4:], re.M)
            if word.search(text[4:4 + end.start()] if end else text[4:]):
                held.append(entry[:-3])
    finally:
        os.close(fd)
    return held, every


def is_added_agent(name, agents_dir=AGENTS_DIR):
    """Whether `name` is an agent file this toolkit wrote (marked; it may since have been changed by hand)."""
    if not isinstance(name, str) or not NAME.fullmatch(name):
        return False
    info = listing(agents_dir).get(name)
    return bool(info and info["added"])


# --- writing ------------------------------------------------------------------------------------------


def create(name, *, description, model, permissions, text, agents_dir=AGENTS_DIR):
    """Write a new added agent's file; refused when any file already holds the name. Returns the bytes."""
    check_name(name)
    description = _clean_text(description, "description", MAX_DESCRIPTION, single=True)
    text = _clean_text(text, "the instruction", MAX_TEXT, single=False)
    if not isinstance(model, str) or not MODEL.fullmatch(model):
        raise AgentfilesError("model must be provider/model (optionally #variant)")
    data = render(description, model, permissions, text)
    with _folder(agents_dir, create=True) as fd:
        taken = _taken(fd, name)
        if taken:
            raise AgentfilesError(f"{name!r} is already an agent file in the agents folder ({', '.join(taken)})")
        _write_at(fd, name + ".md", data, None)
    return data


def replace(name, before, after, *, agents_dir=AGENTS_DIR):
    """Replace (after: bytes) or delete (None) `NAME.md`, or put it back (before None), while it is still `before`."""
    check_name(name)
    with _folder(agents_dir, create=after is not None) as fd:
        if before is None and _taken(fd, name):
            raise AgentfilesError(f"{name!r} is already an agent file in the agents folder; nothing was restored")
        _write_at(fd, name + ".md", after, before)


def _record_folder(backups, kind, name):
    root = Path(backups)
    folder = root / f"{date.today().isoformat()}-agent-files"
    for path in (root, folder):
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            pass
        if not stat.S_ISDIR(os.lstat(path).st_mode):
            raise AgentfilesError(f"{path} is not a plain folder")
    target = folder / f"{kind}-{name}-{secrets.token_hex(6)}"
    os.mkdir(target, 0o700)
    return target


def save_record(backups, kind, name, record):
    """Write an undo record; returns its path (the opaque record a response carries)."""
    folder = _record_folder(backups, kind, name)
    path = folder / "record.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(record, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return str(path)


def prune_records(record):
    """Keep the newest skillset.MAX_BACKUPS undo records of one kind and agent across every day's folder (the
    total stays bounded however many days pass); `record` itself is always kept, and no record among the newest
    MAX_BACKUPS is removed. Only folders holding nothing but a record are removed, and a day's folder left empty."""
    folder = Path(record).parent
    found = re.fullmatch(rf"((?:text|remove|rename)-{names.AGENT_PATTERN})-[0-9a-f]{{12}}", folder.name)
    if not found:
        return
    same = re.compile(re.escape(found.group(1)) + r"-[0-9a-f]{12}")
    try:
        mine = []
        for day in folder.parent.parent.iterdir():
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}-agent-files", day.name) or not stat.S_ISDIR(day.lstat().st_mode):
                continue
            mine += [(day.name, path.lstat().st_mtime_ns, path) for path in day.iterdir()
                     if path != folder and same.fullmatch(path.name) and stat.S_ISDIR(path.lstat().st_mode)]
        mine.sort(key=lambda item: item[:2])  # oldest first: by day, then by time within the day
        for _, _, old in mine[:max(len(mine) + 1 - skillset.MAX_BACKUPS, 0)]:
            if [child.name for child in old.iterdir()] == ["record.json"]:
                (old / "record.json").unlink()
                old.rmdir()
                if old.parent != folder.parent and not any(old.parent.iterdir()):
                    old.parent.rmdir()
    except OSError:
        pass  # pruning is tidying: a record that cannot be removed now is removed by a later change


def drop_record(record):
    """Take back an undo record whose change failed: its file, its folder and the day's folder when left empty."""
    path = Path(record)
    path.unlink(missing_ok=True)
    for folder in (path.parent, path.parent.parent):
        try:
            folder.rmdir()
        except OSError:
            break


RECORD_PATH = re.compile(rf"\d{{4}}-\d{{2}}-\d{{2}}-agent-files/(text|remove|rename)-({names.AGENT_PATTERN})-[0-9a-f]{{12}}"
                         r"/record\.json")


def load_record(record, backups):
    """(kind, name, data) of an undo record path inside the backups folder; refused otherwise."""
    if not isinstance(record, str) or not record or "\0" in record or len(record) > 4096:
        raise AgentfilesError("invalid undo record: expected the record a change returned")
    root = Path(os.path.abspath(backups))
    path = Path(os.path.abspath(record))
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        raise AgentfilesError("invalid undo record: it is not one of the backups folder's undo records") from None
    found = RECORD_PATH.fullmatch(relative)
    if not found:
        raise AgentfilesError("invalid undo record: it is not one of the backups folder's undo records")
    current = root
    for part in path.relative_to(root).parts[:-1]:
        current /= part
        try:
            if not stat.S_ISDIR(current.lstat().st_mode):
                raise AgentfilesError("invalid undo record: its folder is not a plain folder")
        except FileNotFoundError:
            raise AgentfilesError("invalid undo record: there is no such record") from None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise AgentfilesError("invalid undo record: there is no such record") from None
    except OSError as exc:
        raise AgentfilesError(f"invalid undo record: {exc.strerror}") from exc
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise AgentfilesError("invalid undo record: not a regular file")
        raw = stream.read(8 * 1024 * 1024 + 1)
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=skillset._unique)
    except (ValueError, UnicodeDecodeError) as exc:
        raise AgentfilesError(f"invalid undo record: {exc}") from exc
    kind, name = found.group(1), found.group(2)
    if not isinstance(data, dict) or data.get("version") != 1 or data.get("op") != kind or data.get("agent") != name:
        raise AgentfilesError("invalid undo record: it does not match its file name")
    return kind, name, data


def b64(data):
    return None if data is None else base64.b64encode(data).decode("ascii")


def unb64(value, what):
    if value is None:
        return None
    try:
        data = base64.b64decode(value, validate=True)
    except (TypeError, ValueError):
        raise AgentfilesError(f"invalid undo record: {what} is not base64") from None
    if len(data) > FILE_LIMIT:
        raise AgentfilesError(f"invalid undo record: {what} is too large")
    return data


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _change(name, change, *, agents_dir, backups, kind="text", check=None):
    """Apply `change(parsed) -> {"permissions"?, "text"?}` to an added agent's file (`rebuild`: every line the
    toolkit did not write is kept), saving an undo record of the earlier bytes first. `check(data)`, when given, is
    called with the bytes read (the write goes ahead only while they are still there) and may refuse. Returns
    (shown, record path, changed)."""
    check_name(name)
    with _folder(agents_dir) as fd:
        data, parsed = _load(fd, name)
        if check is not None:
            check(data)
        after = rebuild(parsed, **change(parsed))
        if after == data:
            return _shown(name, parse(after)), None, False
        record = save_record(backups, kind, name, {"version": 1, "op": kind, "agent": name, "before": b64(data),
                                                   "after": _digest(after)})
        try:
            _write_at(fd, name + ".md", after, data)
        except BaseException:
            drop_record(record)
            raise
    prune_records(record)
    return _shown(name, parse(after)), record, True


def set_text(name, text, *, agents_dir=AGENTS_DIR, backups=BACKUPS, check=None):
    """Replace an added agent's instruction (`check`: see `_change`). Returns {"name", ..., "changed", "record"?,
    "message"}."""
    text = _clean_text(text, "the instruction", MAX_TEXT, single=False)
    shown, record, changed = _change(name, lambda parsed: {"text": text}, agents_dir=agents_dir, backups=backups,
                                     check=check)
    return {**shown, "changed": changed, **({"record": record} if record else {}), "message": LIVE}


def set_skills(name, skills, *, agents_dir=AGENTS_DIR, backups=BACKUPS, check=None):
    """Give an added agent exactly these skills ("*" alone: every skill; `check`: see `_change`, e.g. from
    `roster.expecting`). Returns (shown, record, changed)."""
    for skill in skills:
        if skill != "*":
            skillset._name(skill, "skill")
    return _change(name, lambda parsed: {"permissions": with_skills(parsed["permissions"], skills)},
                   agents_dir=agents_dir, backups=backups, check=check)


def restore_text(record, *, agents_dir=AGENTS_DIR, backups=BACKUPS):
    """Put back the file a text or skills change replaced, while it is still what that change left. Returns the
    same shape as `set_text`, with a record that undoes this in turn."""
    kind, name, data = load_record(record, backups)
    if kind != "text" or set(data) != {"version", "op", "agent", "before", "after"} or not isinstance(data["after"], str):
        raise AgentfilesError("invalid undo record: not a text change")
    before = unb64(data["before"], "the earlier file")
    if not is_added(before) or parse(before) is None:
        raise AgentfilesError("invalid undo record: the earlier file is not an added agent's")
    with _folder(agents_dir) as fd:
        current = _read_at(fd, name + ".md")
        if current is None or _digest(current) != data["after"]:
            raise AgentfilesError(f"{name}.md was changed after that edit; nothing was restored")
        undo = save_record(backups, "text", name, {"version": 1, "op": "text", "agent": name, "before": b64(current),
                                                   "after": _digest(before)})
        try:
            _write_at(fd, name + ".md", before, current)
        except BaseException:
            drop_record(undo)
            raise
    prune_records(undo)
    return {**_shown(name, parse(before)), "changed": True, "record": undo, "message": LIVE}
