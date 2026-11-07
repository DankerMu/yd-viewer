## ADDED Requirements

### Requirement: IFS precipitation noise tolerance follows the effective GRIB quantization
IFS `tp` de-accumulation SHALL treat a negative delta as quantization noise when `delta ≥ −tol·(1+1e-6)`, with `tol = max(0.1 mm, S_prev, S_curr)`. S is a message's effective quantization step: the file step `1000 × 2^binaryScaleFactor × 10^(−decimalScaleFactor)` mm multiplied by the gcd of the integer offsets `rint((v − min v)/step)`. A message SHALL contribute no S when its scale keys are missing or non-integer, when `bitsPerValue` is 0, or when the gcd is 0; when neither message has an S, tol SHALL be 0.1 mm. Output values SHALL stay `max(0, delta)` rates. A non-ready canonical conversion SHALL raise `ProductionAttemptError` whose message carries a bounded readiness evidence summary.

#### Scenario: Coarse-lattice noise under 12-bit packing stays usable
- **WHEN** two consecutive IFS tp messages are 12-bit encoded with file step 0.1220703125 mm, the values lie on a lattice of 4 file steps, and a grid point's delta is −3 file steps
- **THEN** the product quality flag is `ok`, a `small_negative_ifs_precipitation_delta` anomaly records `tolerance_mm` 0.48828125, and the cycle can reach `canonical_ready`

#### Scenario: Larger negative delta is still flagged
- **WHEN** S is 0.1221 mm for both messages and a delta is −0.2442 mm
- **THEN** the product is `warning_negative_precip` with a `negative_ifs_precipitation_delta` anomaly

#### Scenario: No step metadata keeps legacy behavior
- **WHEN** quantization metadata is unavailable (NetCDF fallback or missing keys) and a delta is −0.12 mm
- **THEN** the product is `warning_negative_precip` exactly as before this change

#### Scenario: Constant field does not inflate tolerance
- **WHEN** the previous tp message is a constant field encoded with `bitsPerValue` 0, the next message's S is below 0.5 mm, and a delta is −0.5 mm
- **THEN** the product is `warning_negative_precip`

#### Scenario: Incomplete conversion explains itself
- **WHEN** canonical conversion ends `canonical_incomplete` with missing lead 51 for `prcp_rate_or_amount`
- **THEN** the raised `ProductionAttemptError` message contains `missing_leads` and lead 51
