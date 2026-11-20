use_when: The final gate, once after substantial or risky work: multi-file changes, core logic, security, data migrations

Reads a finished change independently, looking for bugs, risks and gaps against its intent. The orchestrator runs it once after substantial or risky work, such as multi-file changes, security or data migrations. It hands back findings ranked by severity and a verdict: confirmed, refuted or unverifiable.
