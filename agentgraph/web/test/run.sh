#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
CHROME=${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}
BASE=${TMPDIR:-/private/var/folders/pm/r8xm1mr525v913lct5tdg_5w0000gn/T/opencode}
WORK=$(mktemp -d "$BASE/agentgraph-view.XXXXXX")
trap 'rm -rf "$WORK"' EXIT HUP INT TERM
python3 - "$HERE" "$WORK" <<'PY'
import json, pathlib, sys
from html.parser import HTMLParser
here, work = map(pathlib.Path, sys.argv[1:])
class Scripts(HTMLParser):
    def __init__(self): super().__init__(); self.active = False; self.parts = []
    def handle_starttag(self, tag, attrs):
        if tag == 'script': self.active = not dict(attrs).get('src')
    def handle_endtag(self, tag):
        if tag == 'script': self.active = False
    def handle_data(self, data):
        if self.active: self.parts.append(data)
parser = Scripts(); parser.feed((here.parent / 'index.html').read_text())
page = (here / 'view-test.html').read_text().replace('/* VIEW_SOURCE */', (here.parent / 'app.js').read_text()).replace('/* VIEW_CSS */', (here.parent / 'style.css').read_text()).replace('/* INDEX_BOOTSTRAP */', json.dumps('\n'.join(parser.parts)))
(work / 'view-test.html').write_text(page)
PY
# Real frames matter here: Chrome's virtual-time clock can exhaust its budget
# while requestAnimationFrame/ResizeObserver are still waiting to run.
node "$HERE/run.mjs" "$CHROME" "$WORK"
