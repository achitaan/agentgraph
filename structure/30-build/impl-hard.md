escalates_to: oracle
use_when: Subtle correctness, numerics or performance; large refactors; bugs that survived two attempts

Takes the genuinely difficult implementations: subtle correctness, large refactors, and bugs that survived two attempts. The orchestrator sends work here after the lighter implementers fail. It hands back the fix with evidence from the evaluation, and escalates to oracle when it needs a diagnosis.
