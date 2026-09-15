# NWM@8ae9b8f2 workers/canonical_converter/converter.py
"""yd structural glue: imports.

Moved snapshot bodies retain existing registered adaptations.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta
from typing import Any

from yd_producer.canonical._common import (
    _mapping_value,
    _stable_identity,
    canonical_product_is_forcing_usable,
    format_cycle_time,
    parse_cycle_time,
    required_standard_variables_for_source,
)
from yd_producer.canonical._types import (
    FORCING_USABLE_CANONICAL_QUALITY_FLAGS,
    GFS_F000_OPTIONAL_INTERVAL_STANDARD_VARIABLES,
    CanonicalProductResult,
    CanonicalReadinessResult,
)
from yd_producer.raw.source_identity import normalize_source_id


def evaluate_canonical_readiness(
    *,
    source_id: str,
    cycle_time: str | datetime,
    products: Sequence[Any],
    forecast_hours: Sequence[int] | None = None,
    policy_identity: Mapping[str, Any] | None = None,
    source_object_identity: Mapping[str, Any] | None = None,
    canonical_product_id: str | None = None,
    model_id: str | None = None,
    basin_id: str | None = None,
) -> CanonicalReadinessResult:
    normalized_source = normalize_source_id(source_id)
    parsed_cycle_time = parse_cycle_time(cycle_time)
    required_variables = required_standard_variables_for_source(normalized_source)
    cycle_rows: list[dict[str, Any]] = []
    rejected_quality_flags: dict[str, int] = {}
    rejected_quality_samples: list[dict[str, Any]] = []
    checksum_missing_row_count = 0
    checksum_missing_samples: list[dict[str, Any]] = []
    for product in products:
        row = _canonical_readiness_row(product, default_cycle_time=parsed_cycle_time)
        if str(row.get("source_id") or normalized_source) != normalized_source:
            continue
        if (
            parse_cycle_time(row.get("cycle_time", parsed_cycle_time))
            != parsed_cycle_time
        ):
            continue
        cycle_rows.append(row)
        quality_flag = str(row.get("quality_flag") or "ok")
        checksum = str(row.get("checksum") or "").strip()
        if quality_flag not in FORCING_USABLE_CANONICAL_QUALITY_FLAGS:
            rejected_quality_flags[quality_flag] = (
                rejected_quality_flags.get(quality_flag, 0) + 1
            )
            if len(rejected_quality_samples) < 10:
                rejected_quality_samples.append(
                    _readiness_rejected_row_sample(row, reason="quality_flag_not_ok")
                )
        if quality_flag in FORCING_USABLE_CANONICAL_QUALITY_FLAGS and not checksum:
            checksum_missing_row_count += 1
            if len(checksum_missing_samples) < 10:
                checksum_missing_samples.append(
                    _readiness_rejected_row_sample(row, reason="checksum_missing")
                )
    usable_rows = [
        row for row in cycle_rows if canonical_product_is_forcing_usable(row)
    ]
    expected_hours = sorted(
        {int(hour) for hour in forecast_hours}
        if forecast_hours is not None
        else {
            int(row["lead_time_hours"])
            for row in usable_rows
            if row.get("lead_time_hours") is not None
        }
    )
    variables_by_hour: dict[int, set[str]] = {hour: set() for hour in expected_hours}
    counts_by_variable = {variable: 0 for variable in required_variables}
    lead_counts_by_valid_time: dict[str, int] = {}
    object_identities: set[str] = set()
    policy_identities: set[str] = set()
    identity_rejected_row_count = 0
    missing_policy_identity_row_count = 0
    missing_source_object_identity_row_count = 0
    missing_required_lineage_row_count = 0

    expected_policy_id = _stable_identity(policy_identity)
    expected_object_id = _stable_identity(source_object_identity)

    for row in usable_rows:
        variable = str(row.get("variable") or "")
        if variable not in required_variables:
            continue
        try:
            lead_hour = int(row["lead_time_hours"])
        except (KeyError, TypeError, ValueError):
            continue
        if expected_hours and lead_hour not in variables_by_hour:
            continue
        lineage = _mapping_value(row.get("lineage_json"))
        row_policy = _stable_identity(
            lineage.get("policy_identity")
            or lineage.get("source_policy")
            or lineage.get("canonical_policy_identity")
        )
        row_object = _stable_identity(
            lineage.get("source_object_identity")
            or lineage.get("source_identity")
            or lineage.get("object_identity")
        )
        missing_required_lineage = False
        if expected_policy_id and not row_policy:
            missing_policy_identity_row_count += 1
            missing_required_lineage = True
        if expected_object_id and not row_object:
            missing_source_object_identity_row_count += 1
            missing_required_lineage = True
        if (expected_policy_id and row_policy != expected_policy_id) or (
            expected_object_id and row_object != expected_object_id
        ):
            identity_rejected_row_count += 1
            if missing_required_lineage:
                missing_required_lineage_row_count += 1
            continue
        if row_policy:
            policy_identities.add(row_policy)
        if row_object:
            object_identities.add(row_object)
        variables_by_hour.setdefault(lead_hour, set()).add(variable)
        counts_by_variable[variable] = counts_by_variable.get(variable, 0) + 1
        valid_time = row.get("valid_time")
        valid_time_text = (
            parse_cycle_time(valid_time).isoformat()
            if isinstance(valid_time, str | datetime)
            else ""
        )
        if valid_time_text:
            lead_counts_by_valid_time[valid_time_text] = (
                lead_counts_by_valid_time.get(valid_time_text, 0) + 1
            )

    missing_variables = [
        variable
        for variable in required_variables
        if counts_by_variable.get(variable, 0) == 0
    ]
    missing_leads = []
    for lead_hour in expected_hours:
        required_for_lead = set(required_variables)
        if normalized_source == "gfs" and lead_hour == 0:
            required_for_lead -= set(GFS_F000_OPTIONAL_INTERVAL_STANDARD_VARIABLES)
        present_for_lead = variables_by_hour.get(lead_hour, set())
        if not required_for_lead.issubset(present_for_lead):
            missing_leads.append(
                {
                    "lead_time_hours": lead_hour,
                    "valid_time": (
                        parsed_cycle_time + timedelta(hours=lead_hour)
                    ).isoformat(),
                    "missing_variables": sorted(required_for_lead - present_for_lead),
                    "present_variable_count": len(present_for_lead),
                    "required_variable_count": len(required_for_lead),
                }
            )
    identity_mismatch = bool(
        (expected_policy_id and not policy_identities)
        or (expected_object_id and not object_identities)
        or (identity_rejected_row_count and (missing_variables or missing_leads))
    )
    ready = (
        bool(expected_hours)
        and not missing_variables
        and not missing_leads
        and not identity_mismatch
    )
    status = "canonical_ready" if ready else "canonical_incomplete"
    unusable_required_row_count = (
        sum(rejected_quality_flags.values()) + checksum_missing_row_count
    )
    evidence = {
        "source": normalized_source,
        "source_id": normalized_source,
        "cycle_time": parsed_cycle_time.isoformat(),
        "status": status,
        "ready": ready,
        "canonical_product_id": canonical_product_id
        or f"canon_{normalized_source.lower()}_{format_cycle_time(parsed_cycle_time)}",
        "model_id": model_id,
        "basin_id": basin_id,
        "required_variables": list(required_variables),
        "present_variables": sorted(
            variable for variable, count in counts_by_variable.items() if count > 0
        ),
        "missing_variables": missing_variables,
        "expected_leads": expected_hours,
        "accepted_horizon": {
            "first_lead_hour": min(expected_hours) if expected_hours else None,
            "last_lead_hour": max(expected_hours) if expected_hours else None,
            "lead_count": len(expected_hours),
        },
        "per_valid_time_lead_counts": lead_counts_by_valid_time,
        "missing_leads": missing_leads,
        "row_count": len(usable_rows),
        "candidate_row_count": len(cycle_rows),
        "rejected_quality_flags": rejected_quality_flags,
        "rejected_quality_samples": rejected_quality_samples,
        "checksum_missing_row_count": checksum_missing_row_count,
        "checksum_missing_samples": checksum_missing_samples,
        "policy_identity": dict(policy_identity or {}),
        "source_object_identity": dict(source_object_identity or {}),
        "policy_identity_matched": not expected_policy_id or bool(policy_identities),
        "source_object_identity_matched": not expected_object_id
        or bool(object_identities),
        "identity_rejected_row_count": identity_rejected_row_count,
        "missing_policy_identity_row_count": missing_policy_identity_row_count,
        "missing_source_object_identity_row_count": missing_source_object_identity_row_count,
        "missing_required_lineage_row_count": missing_required_lineage_row_count,
        "reused_existing_ready": ready,
    }
    if identity_mismatch:
        if (
            identity_rejected_row_count > 0
            and missing_required_lineage_row_count == identity_rejected_row_count
            and not (policy_identities and object_identities)
        ):
            evidence["reason"] = "canonical_lineage_missing"
        elif not usable_rows and unusable_required_row_count > 0:
            evidence["reason"] = (
                "missing_canonical_variables"
                if missing_variables
                else "missing_canonical_leads"
            )
        else:
            evidence["reason"] = "canonical_identity_mismatch"
    elif missing_variables:
        evidence["reason"] = "missing_canonical_variables"
    elif missing_leads:
        evidence["reason"] = "missing_canonical_leads"
    elif not expected_hours:
        evidence["reason"] = "no_expected_leads"
    return CanonicalReadinessResult(status=status, ready=ready, evidence=evidence)


def _canonical_readiness_row(
    product: Any, *, default_cycle_time: datetime
) -> dict[str, Any]:
    if isinstance(product, Mapping):
        return dict(product)
    if is_dataclass(product) and not isinstance(product, type):
        row = {
            dataclass_field.name: getattr(product, dataclass_field.name)
            for dataclass_field in fields(product)
        }
    else:
        row = {}
        for key in (
            "canonical_product_id",
            "source_id",
            "cycle_time",
            "valid_time",
            "lead_time_hours",
            "variable",
            "object_uri",
            "checksum",
            "quality_flag",
            "lineage_json",
        ):
            if hasattr(product, key):
                row[key] = getattr(product, key)
    if row.get("lead_time_hours") is None and row.get("valid_time") is not None:
        cycle_time = parse_cycle_time(row.get("cycle_time", default_cycle_time))
        valid_time = parse_cycle_time(row["valid_time"])
        row["lead_time_hours"] = int((valid_time - cycle_time).total_seconds() // 3600)
    return row


def _readiness_rejected_row_sample(
    row: Mapping[str, Any], *, reason: str
) -> dict[str, Any]:
    sample: dict[str, Any] = {
        "reason": reason,
        "variable": str(row.get("variable") or ""),
        "quality_flag": str(row.get("quality_flag") or "ok"),
    }
    if row.get("lead_time_hours") is not None:
        try:
            sample["lead_time_hours"] = int(row["lead_time_hours"])
        except (TypeError, ValueError):
            sample["lead_time_hours"] = row.get("lead_time_hours")
    if row.get("valid_time") is not None:
        try:
            sample["valid_time"] = parse_cycle_time(row["valid_time"]).isoformat()
        except (TypeError, ValueError):
            sample["valid_time"] = str(row.get("valid_time"))
    return sample


def _canonical_product_result_readiness_row(
    product: CanonicalProductResult,
    *,
    source_id: str,
    cycle_time: datetime,
) -> dict[str, Any]:
    return {
        "canonical_product_id": product.canonical_product_id,
        "source_id": source_id,
        "cycle_time": cycle_time,
        "valid_time": product.valid_time,
        "lead_time_hours": product.lead_time_hours,
        "variable": product.variable,
        "object_uri": product.object_uri,
        "checksum": product.checksum,
        "quality_flag": product.quality_flag,
        "lineage_json": dict(product.lineage_json),
    }


def _canonical_readiness_error_message(evidence: Mapping[str, Any]) -> str:
    reason = str(evidence.get("reason") or "canonical_incomplete")
    details: dict[str, Any] = {"reason": reason}
    for key in (
        "missing_variables",
        "missing_leads",
        "rejected_quality_flags",
        "checksum_missing_row_count",
        "identity_rejected_row_count",
        "missing_required_lineage_row_count",
    ):
        value = evidence.get(key)
        if value not in (None, [], {}, 0):
            details[key] = value
    return json.dumps(details, sort_keys=True, default=str)
