"""Hidden consumer check for t4. Run with the work folder as the current directory."""
import os
import sys
import tempfile

sys.path.insert(0, ".")
import app  # noqa: E402
import config  # noqa: E402

bad = []
folder = tempfile.mkdtemp()


def write(text):
    path = os.path.join(folder, "c.conf")
    with open(path, "w", encoding="utf-8") as stream:
        stream.write(text)
    return path


try:
    got = config.load(write('# top\n   # indented comment\nurl = http://x/?a=b&c=d\nnote = "  keep # this = and spaces  "\n'
                            'empty = ""\nn=1\nn=2\n'))
    want = {"url": "http://x/?a=b&c=d", "note": "  keep # this = and spaces  ", "empty": "", "n": "2"}
    if got != want:
        bad.append(f"load gave {got!r}")
except Exception as error:  # noqa: BLE001
    bad.append(f"load raised {error!r}")
try:
    config.load(write("a = 1\n\n# c\nbroken line\nb = 2\n"))
    bad.append("no ConfigError")
except config.ConfigError as error:
    if "line 4" not in str(error):
        bad.append(f"message {str(error)!r} does not say line 4")
except Exception as error:  # noqa: BLE001
    bad.append(f"wrong error {error!r}")
path = write("port = 9001\nname = \"web # one\"\n")
if app.settings(path) != {"port": 9001, "name": "web # one"}:
    bad.append(f"app.settings = {app.settings(path)!r}")
if app.settings(write("# nothing\n")) != {"port": 8080, "name": "service"}:
    bad.append("app.settings defaults")
try:
    config.get_int({"p": "x"}, "p")
    bad.append("get_int accepted junk")
except config.ConfigError:
    pass
print("hidden t4:", "FAIL " + "; ".join(bad) if bad else "ok")
sys.exit(1 if bad else 0)
