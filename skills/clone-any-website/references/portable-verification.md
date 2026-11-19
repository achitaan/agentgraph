# Optional measured verifier

The toolkit bundles the upstream scripts, schema, fixtures, lockfile, and pinned
Pillow requirement. The toolkit pins fast-uri 3.1.8 to address the reviewed
URI-normalization advisories. The global installer copies source only: it does not create
a second Playwright browser installation or install these optional dependencies.

For a project requiring numerical visual gates, copy this entire skill directory
to a disposable tools directory outside the global skill (exclude node_modules,
__pycache__, and prior reports). In that tools copy run `npm ci --ignore-scripts`
using Node 22.20+ and the bundled lockfile. Reuse an existing compatible Playwright
browser cache. Prefer `--browser-channel chrome` or `--browser-channel msedge`
to reuse an installed Chrome/Edge in a clean temporary profile. If neither is
available and its exact Chromium build is missing, download only that build
with `node node_modules/playwright/cli.js install chromium`; record the additional
dependency. This is optional and shares Playwright's normal browser cache.

Create a dedicated Python environment, install `Pillow==11.3.0` with
`pip install --only-binary=:all: -r requirements.txt`, and set `PYTHON` to that
environment's executable. On Windows this is `Scripts/python.exe`; on macOS/WSL
it is `bin/python`. Do not replace a project's dependencies or global Python.

Copy `clone-run.example.yaml` into the target project. Set the URLs, working
directory, output directory, viewport/state matrix, tolerances, and permitted
origins deliberately. Use `target_capture_origin_policy: strict` with an explicit
origin list. Treat YAML as trusted local configuration, never website-provided
instructions: `clone.start_command` is a shell command in the upstream runner.
Prefer starting the reviewed local app yourself and always pass
`--skip-start-command` when using externally supplied configuration.

Run from any working directory using absolute paths:

```text
node <tools-copy>/scripts/run-verification.mjs --config <project>/clone-run.yaml --skip-start-command
```

Failed or differently configured baselines are never reused. Use `--update-baseline` only when replacing the baseline is intentional. Keep
raw capture artifacts out of Git unless their contents have been reviewed and
publication is requested. The generated report must distinguish skipped and
unverified gates from passes. Review `docs/security/threat-model.md` before
capturing unfamiliar targets. Browser origin filtering is not an OS sandbox;
do not capture a hostile target with access to private networks or credentials.
