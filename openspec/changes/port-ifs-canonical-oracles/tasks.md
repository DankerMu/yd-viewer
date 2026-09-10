## 1. Snapshot implementation
- [ ] 1.1 Register full pin15 scenario/helper adaptation row in inventory before source test, amend prior excluded-test mention, preserve source provenance.
- [ ] 1.2 Port all15 scenarios to producer/tests/test_ifs_canonical.py with DB-free catalog lookup, explicit config paths and current lowercase canonical IDs; no runtime edits.
- [ ] 1.3 Parametrize three precipitation branches independently (total17cases), preserve/add required anomalies/quality/counter; retain all shortwave/RH/lineage oracles and missing-ssr no-output behavior.
- [ ] 1.4 Mark snapshot landed and close issue13 known coverage loss after proving implementation; no test threshold/source special-case.
## 2. Evidence and delivery
- [ ] 2.1 Baseline new17cases green and all15scenario mapping confirmed; no NWM/DB runtime dependencies or outbound connections.
- [ ] 2.2 Parent runs eight single-line branch mutants: old full producer suite excluding new file green, new tests red, exact commands/results per row; positive control proves module/export binding.
- [ ] 2.3 Parent profile matrix serial producer/viewer pytest, ruff/OpenSpec/stage log green; runtime source diff exactly empty.
- [ ] 2.4 Fixture review+strict validation before implementation; comprehensive/final review/CI/evidence/preauthorized merge+archive/log tracked in PR.
