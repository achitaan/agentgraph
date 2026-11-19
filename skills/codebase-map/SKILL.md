---
name: codebase-map
description: Map a repository's modules, dependencies, callers, and data flows with source-backed graphs. Use for codebase understanding, architecture visualization, and cross-module change impact; a small known edit usually needs direct source search.
---

# Codebase map

Build a useful map from available source tools. This is a guided mapping workflow,
not a bundled Graphify/GitNexus parser, automatic index, or scheduling engine.

For this personal toolkit specifically, run
`python scripts/toolkit_map.py <toolkit-checkout>` from this skill's folder to regenerate its offline HTML,
Markdown, and JSON maps. The helper inventories manifest skills and static local
Python imports; it does not infer a complete semantic graph of arbitrary projects.

1. Establish repository root, revision, dirty state, and the question. For an
   entire-codebase request, inventory tracked files and module/build boundaries
   first. Include relevant untracked source deliberately. Report ignored,
   generated, vendored, unsupported, and unavailable areas rather than claiming
   every symbol was parsed.
2. Start with a module-level view. Use imports, exports, manifests, route/handler
   registration, schemas, tests, and current caller search. Follow only the
   detailed subgraph needed for the question. Avoid a giant unreadable hairball.
3. Use [graph evidence](references/graph-evidence.md) to define edge meaning,
   direction, source locations, confidence, and freshness. Verify dynamic edges
   through registration or runtime evidence where possible.
4. Produce a readable Mermaid overview and an evidence table. For a reusable
   graph, include a simple node/edge data file with the same evidence fields.
   Link a focused subgraph for deeper questions. Use an available visualization
   tool if interaction helps; a browser graph is optional.
5. Validate the relationships needed for the conclusion against current source.
   Record coverage and open questions. For a change, trace direct and transitive
   dependents and relevant tests, then inspect the actual code before editing.

Use an existing approved graph engine if it is available and its output helps;
verify its version, supported languages, privacy settings, and freshness. Do not
install or call a hosted semantic provider automatically. Local source search is
a valid fallback; describe its coverage honestly.

Keep code relationships separate from task prerequisites and linked knowledge
notes. A code edge suggests where to investigate; it does not prove that a task
must run, that a path is safe, or that no other consumers exist.
