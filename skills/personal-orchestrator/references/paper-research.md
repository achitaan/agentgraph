# Targeted paper research

Use when a Researcher assignment depends on an algorithm, scientific claim,
mathematical method or unfamiliar trade-off that published evidence could change.
The user can also explicitly request a literature comparison. Keep routine code
questions with project source and current documentation when those suffice.

## Find evidence that can change the decision

- Frame a bounded question from the task, constraints and current evidence.
  Identify what finding would change the next implementation or conclusion.
- Use the available Exa search/fetch tools for public discovery, or the host's
  supported web search if unavailable. Search both recent relevant work and
  foundational or influential papers. “Big” means relevant established work;
  fame, citations and recency alone do not establish correctness or suitability.
- Prefer primary papers, author pages and official companion repositories.
  Check publication date, version and whether the work is a preprint. Read the
  methods and limitations supporting the claim, not only a snippet or abstract.
  Verify important claims against the paper; a repository or benchmark needs its
  own provenance. Never invent inaccessible results, equations or citations.
- Start with one focused discovery pass and the few sources that can resolve
  the question, typically two to four. Follow another lead only for a named gap
  within the shared time/launch allowance. An existing supplied-source scope or
  offline-only instruction takes precedence; report unavailable evidence.
- Send only appropriate public concepts to search. Keep private code, traces,
  unpublished findings, credentials and private URLs out of hosted queries.

## Test applicability

Match assumptions to the actual inputs, precision, hardware, memory, dependencies
and correctness requirements. Separate theoretical results, author-reported
measurements, implementation availability and locally reproduced evidence.
Mark conflicting findings and untested assumptions. Do not promise a local
speedup from a paper's benchmark or silently adopt its relaxed correctness bar.

Return a compact memo with:

1. The question and recommendation, or the remaining uncertainty.
2. Source links, titles, dates/versions and the relevant section or result.
3. A few candidate hypotheses, applicability limits and why each matters here.
4. Concrete correctness or measurement checks that could reject the suggestion.

For mathematical work, preserve proof assumptions and distinguish derivation from
numerical evidence. For an implementation task, the main conversation decides
what to change and verifies it locally. A delegated Researcher remains read-only
and does not launch other workers. Review receives the original sources and
current results alongside the memo so its claims can be checked independently.
