## 1. Precision-only fix
- [x] 1.1 Inventory §1 producer.py row37 (target producer/src/yd_producer/forcing/producer.py) already carries the #119 problem+fix in 剥离点 before code; never use converter row36 or module header as a substitute. Add matching module deviation note and correct stale row-number attribution by target-path identity.
- [x] 1.2 Change only five station geometry formatting expressions to repr(float), retaining existing defaults/coercions and _format_number for all timeseries.
- [x] 1.3 Add independent complete index byte literal plus exact five-field float readback and unchanged timeseries literal in existing test_forcing_helpers_yd.py; no newcode/test files or guard exemptions.
## 2. Verification and delivery
- [x] 2.1 Parent runs new geometry oracle on old producer (red) then fixed(green), and direct formatter smoke confirms contract geometry.
- [x] 2.2 Existing test_forcing_producer.py and round2boundary suite, then serial fullproducer/viewer/ruff/OpenSpec/stage log green; edited yd test under1000lines.
- [x] 2.3 Fixture review+strict validation; comprehensive/final review, CI, frozen-tip preauthorized merge+archive/log tracked in PR evidence.
