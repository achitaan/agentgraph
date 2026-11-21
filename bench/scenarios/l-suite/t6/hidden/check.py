"""Hidden check for t6: every rule of goal.md, reported by rule number. Run with the work folder as the current directory."""
import sys

sys.path.insert(0, ".")
from settings import SettingsError, parse_settings  # noqa: E402

bad = []


def ok(rule, text, want):
    try:
        got = parse_settings(text)
    except Exception as error:  # noqa: BLE001
        got = f"raised {error!r}"
    if got != want:
        bad.append((rule, f"{text!r}: got {got!r}, expected {want!r}"))


def err(rule, text, line, contains=None):
    try:
        got = parse_settings(text)
    except SettingsError as error:
        message = str(error)
        if not message.startswith(f"line {line}: ") or (contains and contains not in message):
            bad.append((rule, f"{text!r}: message {message!r} should start with 'line {line}: '" + (f" and name {contains!r}" if contains else "")))
        return
    except Exception as error:  # noqa: BLE001
        bad.append((rule, f"{text!r}: wrong exception {error!r}"))
        return
    bad.append((rule, f"{text!r}: no error, got {got!r}"))


ok(1, "# c\n; c2\n\n   # indented\n  ;x\na=1\n", {"a": "1"})
ok(1, "", {})
ok(2, "a = b = c\n  k2=  v2  \n", {"a": "b = c", "k2": "v2"})
ok(2, "url=http://h/?x=1&y=2\n", {"url": "http://h/?x=1&y=2"})
ok(3, "x=0\n[s]\na=1\n[ t ]\nb=2\n  [s]  \nc=3\n[S]\nd=4\n", {"x": "0", "s.a": "1", "t.b": "2", "s.c": "3", "S.d": "4"})
err(4, "[a\n", 1)
err(4, "[a] x\n", 1)
err(4, "[]\n", 1)
err(4, "[ ]\n", 1)
err(4, "\n\n[oops\nk=1\n", 3)
ok(4, "[a] # c\nk=1\n", {"a.k": "1"})
err(5, "a=1\nnonsense\n", 2)
err(6, "\n= v\n", 2)
err(6, "  =\n", 1)
ok(7, "a=\nb =   \n", {"a": "", "b": ""})
ok(8, 'a = "  hi  "\n', {"a": "  hi  "})
ok(8, 'b = "x # y ; z"\n', {"b": "x # y ; z"})
ok(8, 'c = "q\\"r\\\\s\\nt\\tu"\n', {"c": 'q"r\\s\nt\tu'})
ok(8, 'd = ""\n', {"d": ""})
ok(8, 'e = "x"   # c\n', {"e": "x"})
ok(8, 'f = "a=b"\n', {"f": "a=b"})
err(8, 'a = "abc\n', 1)
err(8, 'a = "a\\qb"\n', 1)
err(8, 'a = "x" y\n', 1)
err(8, 'a = "abc\\"\n', 1)
ok(9, "a = x#y\nb = x #y\nc = # none\nd = p  # q # r\n", {"a": "x#y", "b": "x", "c": "", "d": "p"})
ok(9, "e = x ; y\n", {"e": "x ; y"})
err(10, "a=1\n[s]\na=2\na=3\n", 4, "s.a")
err(10, "a=1\nb=2\na=3\n", 3, "a")
ok(10, "a=1\nA=2\n", {"a": "1", "A": "2"})
ok(10, "[s]\na=1\n[t]\na=2\n", {"s.a": "1", "t.a": "2"})
ok(10, "[s]\na=1\n[s]\nb=2\n", {"s.a": "1", "s.b": "2"})
err(11, "# a\n\n; b\nbad\n", 4)
err(11, "a=1\r\nbad\r\n", 2)
err(11, "ok=1\nbad one\n[x\n", 2)
ok(11, "a=1\r\nb=2\r\n", {"a": "1", "b": "2"})

import app  # noqa: E402

for text, want in (("debug = on\n[server]\nhost = h\nport = 9\n", "h:9 debug=True"), ("port = 1\n", "localhost:8080 debug=False"),
                   ("[server]\nport = x\n", "settings rejected: server.port is not a number: 'x'")):
    got = app.describe(text)
    if got != want:
        bad.append((12, f"app.describe({text!r}) = {got!r}, expected {want!r}"))

rules = sorted({r for r, _ in bad})
print("hidden t6:", "FAIL rules " + ", ".join(map(str, rules)) + " | " + "; ".join(m[:150] for _r, m in bad[:4]) if bad else "ok")
sys.exit(1 if bad else 0)
