pool: 2
escalates_to: impl-hard

Takes the bigger tasks: changes across several files or modules, work that needs a lot of code reading, or design choices left open. The orchestrator briefs it with a loop item. It hands back the changes with the evaluation result, and a failed task moves up to impl-hard.
