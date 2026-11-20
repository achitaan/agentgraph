# Upstream sources

This includes a personal snapshot of upstream skills and locally maintained skills.
The toolkit does not claim authorship of the retained upstream bundles.
`manifest.json` records exact source commits and package versions. `licenses/`
and each plugin/skill's existing license files preserve upstream notices.

| Bundle | Upstream |
|---|---|
| Playwright CLI skill | [microsoft/playwright-cli](https://github.com/microsoft/playwright-cli) |
| Supabase skills | [supabase/agent-skills](https://github.com/supabase/agent-skills) |
| Context7 skill | [upstash/context7](https://github.com/upstash/context7) |
| Find-Skills | [vercel-labs/skills](https://github.com/vercel-labs/skills) |
| Grill-me and Grilling | [mattpocock/skills](https://github.com/mattpocock/skills) |
| Taste | [Leonxlnx/taste-skill](https://github.com/Leonxlnx/taste-skill) |
| Math Research | [jmf-enigma/math-research](https://github.com/jmf-enigma/math-research) |
| SymPy and Experimental Design | [K-Dense-AI/scientific-agent-skills](https://github.com/K-Dense-AI/scientific-agent-skills) |
| ML Experiment Standards | [muend/geoai-skills](https://github.com/muend/geoai-skills) |
| Research Software Engineering | [a-attia/scicomp-research-skills](https://github.com/a-attia/scicomp-research-skills) |
| Performance Speedup | [KirillKlem/codex-skills](https://github.com/KirillKlem/codex-skills) |
| Codex AutoResearch | [leo-lilinxiao/codex-autoresearch](https://github.com/leo-lilinxiao/codex-autoresearch) |
| UI-UX-Pro-Max | [nextlevelbuilder/ui-ux-pro-max-skill](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill) |
| Humanizer | [blader/humanizer](https://github.com/blader/humanizer) |
| Writing Clearly and Concisely | [obra/the-elements-of-style](https://github.com/obra/the-elements-of-style) |
| Internal Comms | [anthropics/skills](https://github.com/anthropics/skills) |
| Research Summarizer | [alirezarezvani/claude-skills](https://github.com/alirezarezvani/claude-skills) |
| Impeccable portable guidance | [pbakaus/impeccable](https://github.com/pbakaus/impeccable), Apache-2.0 |
| Clone Any Website | [promptwhisper/clone-any-website-skill](https://github.com/promptwhisper/clone-any-website-skill), MIT |
| Optional Next.js template | [JCodesMore/ai-website-cloner-template](https://github.com/JCodesMore/ai-website-cloner-template), MIT |
| Usage-accounting adaptation | [LaneOrchestrator source](https://github.com/KarthikRamesh9149/laneorchestrator/tree/9890a6b96675d1fb129bf44ea560a492ead2c780), MIT; retained notice (removed with `personal-orchestrator` on 2026-11-20; in the git history) |

The upstream orchestration plugin and VoltAgent catalog are no longer bundled.
Only the locally adapted usage-accounting code is retained, with its original
MIT notice and provenance (both removed with the `personal-orchestrator` skill on 2026-11-20; in the git history).
Local adaptations: Grill-me's Codex alias, explicit invocation metadata for
interviews/AutoResearch, and a valid RSE YAML description. Full workflows
remain upstream material. Binary CLIs and Python libraries are downloaded from
their official registries/releases, not redistributed in this repository.

The Performance Speedup source repository has no root license at the recorded
commit; its material is retained for this private personal snapshot, with all
upstream rights reserved. Do not treat the installer's MIT license as permission
to republish that material. UI-UX-Pro-Max's copied root license was retrieved on
the snapshot date; its bundled skill provenance is the recorded npm package.

The four writing skills are copied without local content changes. Humanizer and
Research Summarizer retain their upstream MIT license notices in `licenses/`;
Humanizer also includes its license in the skill directory. Internal Comms
includes Anthropic's Apache-2.0 `LICENSE.txt`. The Elements of Style repository
has no separate root license file at its recorded commit. Its preserved upstream
README in `licenses/obra--the-elements-of-style--README.md` identifies the 1918
Strunk reference as public-domain text from Project Gutenberg; that attribution
does not assign a separate license to the repository's newer wrapper material.


2026-10-11 adaptations: Impeccable is a portable four-playbook guidance edition,
with upstream license and NOTICE retained, without its engine or hooks. Clone Any
Website has a compact Codex entrypoint, permission-aware guidance, portable test
paths, installed-browser selection, and stale-baseline rejection; upstream code,
assets, and MIT attribution remain. The optional Next.js template excludes agent
configuration and GitHub workflows. Exa Research and the shared installer are
original toolkit material under the root MIT license; hosted service access is
subject to the provider's terms.

Context7, Playwright, and UI-UX-Pro-Max activation descriptions were narrowed
for workflow ownership. Superpowers is no longer distributed by this snapshot.
GitNexus, Website Analyzer, Mimic, Firecrawl, and Trail of Bits were evaluated but
not vendored or installed. Their recorded license findings do not grant usage
rights beyond their actual upstream terms.

The default runtime lockfile preserves npm package license metadata. SkillUI
transitive overrides are simple-git 4.0.1, @simple-git/argv-parser 2.0.1, and
esbuild 0.28.1; source extraction and Git cloning were smoke-tested. The
optional clone verifier and Next.js starter have separate frozen lockfiles.
The verifier overrides fast-uri to3.1.8. The starter has locally patched
Next16.3.8 dependencies and preserves the shadcn stylesheet/license separately;
see its TOOLKIT-HARDENING.md for the remaining development-only advisories.
Hosted Exa, Context7, and Supabase are external services, not bundled binaries.

## 2026-10-25 owned prompt consolidation

The eight skills under `skills/` are locally maintained editions under the root
MIT license. Engineering Delivery, Code Assurance, Project Contracts, Delivery
Operations, and Project Knowledge adapt selected practices from
[Addy Osmani's Agent Skills](https://github.com/addyosmani/agent-skills) at
`aeac41d4d005999432bb7312f7ff06383a91d59d` (0.6.13, MIT). Its copyright and full
license are retained in `licenses/addyosmani--agent-skills--LICENSE` and in each
adapted skill as `LICENSE.addy`, so standalone installation preserves attribution.
These are consolidated rewrites; the upstream pack, shared router, hooks, and
mandatory repeated cross-model prompts are not copied.

Karpathy's measured experiments and LLM Wiki, Graphify's graph concepts, and
plain Markdown use in Obsidian informed the design. No source code or substantial
prompt text from those projects is redistributed by this addition. The optional
Ralph guide describes an existing external runtime; it contains no runtime code.
The portable Ruby guide contains personal general conventions, without private
company references. See `docs/consolidation-sources.md` for reviewed versions,
license boundaries, and platform limitations. Existing upstream licenses and
previously recorded license gaps remain unchanged.
