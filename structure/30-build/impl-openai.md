pool: 3
escalates_to: impl-claude

The default implementer for bounded, well-specified tasks with a clear scope and a check to run. Several of them work in parallel on separate files. It hands back the changes and the evaluation result, and a task that proves larger than specified moves up to impl-claude.
