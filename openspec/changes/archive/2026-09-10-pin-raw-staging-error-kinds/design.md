## Context
Issue #76, profile yd-viewer. Fixture level none, repair low: isolated test assertion for an unchanged contract, no runtime/API/schema/IO behavior changes. Upstream suggested level absent. No expanded trigger applies to changed behavior because production code is untouched. Full artifact set retained for workflow Core Rules.
## Decisions
Import ERROR_KINDS and compare to frozenset of nine fixture literals, not length/subset or Markdown parsing. Mutation proof adds a tenth entry then independently renames one entry, each before importing the new test in a fresh uv process. No new permanent mutation runner.
## Must preserve / Non-Goals
All existing assertions, ERROR_KINDS contents, RawStagingError constructor, admission fallback source-manifest, all branch behavior and dependencies remain unchanged. Do not combine #100 split or expand the floor-kind policy raised in the m2 Known-limit; user explicitly requests only the vocabulary assertion.
## Risks
A source-derived expected set is tautological; the expected set must use fixture literals in test code. Public enum mutation in an isolated interpreter must occur before test import to avoid copied import aliases.
