## ADDED Requirements

### Requirement: IFS precipitation noise tolerance follows GRIB quantization
IFS `tp` de-accumulation SHALL treat a negative delta as quantization noise when its magnitude does not exceed `max(0.1 mm, step_prev, step_curr)`, where each step is the message's GRIB quantization step `1000 × 2^binaryScaleFactor × 10^(−decimalScaleFactor)` mm read through cfgrib. A message with `bitsPerValue == 0` (constant field) or missing scale keys SHALL contribute no step; when no step is available the tolerance SHALL be 0.1 mm. Output values SHALL remain `max(0, delta)` rates regardless of tolerance. A non-ready canonical conversion SHALL raise `ProductionAttemptError` whose message carries a bounded readiness evidence summary.

#### Scenario: One-step noise under 12-bit packing stays usable
- **WHEN** two consecutive IFS tp messages have quantization step 0.1221 mm and a grid point's delta is −0.1221 mm
- **THEN** the product quality flag is `ok`, a `small_negative_ifs_precipitation_delta` anomaly records `tolerance_mm` 0.1221, and the cycle can reach `canonical_ready`

#### Scenario: Larger negative delta is still flagged
- **WHEN** the same messages contain a delta of −0.2442 mm
- **THEN** the product is `warning_negative_precip` with a `negative_ifs_precipitation_delta` anomaly

#### Scenario: No step metadata keeps legacy behavior
- **WHEN** quantization metadata is unavailable (NetCDF fallback or missing keys) and a delta is −0.12 mm
- **THEN** the product is `warning_negative_precip` exactly as before this change

#### Scenario: Constant field does not inflate tolerance
- **WHEN** the previous tp message is a constant field encoded with `bitsPerValue` 0 and the next message has no usable step, and a delta is −0.5 mm
- **THEN** the product is `warning_negative_precip`

#### Scenario: Incomplete conversion explains itself
- **WHEN** canonical conversion ends `canonical_incomplete` with missing lead 51 for `prcp_rate_or_amount`
- **THEN** the raised `ProductionAttemptError` message contains `missing_leads` and lead 51
