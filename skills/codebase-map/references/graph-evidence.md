# Graph evidence and change impact

Record the repository/revision, dirty paths or content fingerprint, generated
time, discovery method, included scope, exclusions, and unsupported mechanisms.
A commit hash alone does not identify a graph generated from an edited worktree.

Use stable repository-relative paths and qualified symbol names for node IDs.
Module nodes may summarize files; do not imply they represent parsed symbols.
Each useful edge has:

| Field | Meaning |
|---|---|
| from, to | Nodes with explicitly declared direction |
| relation | imports, calls, produces, consumes, registers, or tests |
| evidence | Repository-relative file and line, manifest key, or runtime record |
| certainty | observed in source/runtime, or inferred with a stated reason |
| scope | Revision/worktree snapshot and relevant limitations |

For `imports` and `calls`, use dependent/caller -> dependency/callee. Reverse
traversal finds possible consumers affected by a dependency change. For data flow,
use producer -> data contract -> consumer and label those distinct relations.
Don't mix these directions without a legend.

For a whole repository, report counted inventoried files separately from inspected
files and supported parsed files, if a parser was used. Do not call an inventory
“100% semantic coverage.” Dynamic imports, reflection, configuration, dependency
injection, generated code, and external clients can hide edges.

For impact analysis, start at the changed contract, walk dependents, identify
runtime/config boundaries, and map the checks that exercise them. Rank by actual
behavior and consequence, not graph centrality alone. Inspect representative
consumers and boundary cases before proposing a fix.

When code moves, refresh the affected neighborhood and its incoming edges.
Rebuild broader coverage when manifests, module boundaries, generated code, or
parser settings change. Mark a stale map; do not silently reuse it as current.
Validate referenced nodes, source locations, duplicate edges, and a few important
paths manually or with existing graph tools. Absence of a discovered edge is not
proof that no dependency exists.
