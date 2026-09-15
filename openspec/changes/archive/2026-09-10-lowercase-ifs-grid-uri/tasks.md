## 1. Cutover
- [x] 1.1 Inventory authority: converter row36 IFSCanonicalConverterConfig.grid_definition_uri already contains #104 problem+fix; forcing file_store row38 _grid_definition_uri_for_source IFS literal adaptation is also registered before code. Use target-path row identities, not stale historical row35 (table header); update both module ledgers.
- [x] 1.2 Change canonical config and forcing default helper IFS URI only; no new constants/aliases/parser changes/raw capitalization changes.
- [x] 1.3 Update existing yd IFS e2e name/docstring/string and affected forcing fixtures/tests plus raw-containment grid helper; keep real behavior assertions. Rewrite converter GFS/IFS convert_manifest comments still claiming decision1/16 retains uppercase canonical/IFS; refresh header current-scope statements so no current URI exception is claimed. Historical pin config.toml/NWM reference strings stay unchanged.
## 2. Evidence and delivery
- [x] 2.1 Parent old-source red for updated IFS e2e string; final focused canonical/forcing tests green.
- [x] 2.2 Parent smoke: actual lowercase emitted key/case-sensitive string+object_path parsing; products byte-identical and catalog only URI differs; GFS unchanged.
- [x] 2.3 Fixture review+strict validation; parent profile serial producer/viewer/ruff/OpenSpec/stage-log verification.
- [x] 2.4 Comprehensive review/final review/CI/frozen-tip merge and archive/log tracked in PR evidence.
