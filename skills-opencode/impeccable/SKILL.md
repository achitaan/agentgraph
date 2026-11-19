---
name: impeccable
description: Audit, polish, harden, or adapt an existing web interface for accessibility, responsive behavior, usability, and visual consistency. Use for requested UI quality work; Taste owns original landing-page direction and UI-UX-Pro-Max owns app design and reference data.
metadata:
  upstream-version: "4.5.2"
  edition: "portable-guidance"
---

# Impeccable — portable UI quality guidance

This toolkit adapts four Impeccable playbooks for use in OpenCode on macOS,
Windows, and WSL. Use the project's existing checks and Playwright CLI or available browser
tools. This edition does not include the Impeccable engine, live server,
critique-storage, automatic hooks, or downloadable binaries. Do not install
those as a side effect of a UI task or claim that their detector ran.

Read only the playbook that fits the request:

- [audit](references/audit.md): inspect and report technical UI defects.
- [polish](references/polish.md): fix inconsistencies while preserving identity.
- [harden](references/harden.md): improve real edge cases and failure states.
- [adapt](references/adapt.md): make the existing design work across viewports.

Inspect the existing design system, representative components, and rendered
behavior first. Preserve the user's chosen aesthetic, factual copy, task scope,
and existing conventions. An audit alone does not authorize unrelated edits.
Use accessibility standards accurately: distinguish measured failures from
heuristics, and do not claim conformance from a checklist or screenshot alone.

Batch desktop/mobile checks, fix the observed defects, and verify changed paths.
Do not enter an open-ended polish loop. Report the evidence, changes, and any
browser, assistive-technology, or physical-device checks that remain untested.

The upstream Apache-2.0 license is in [LICENSE](LICENSE). This is an adapted
guidance edition, not the complete upstream product.
