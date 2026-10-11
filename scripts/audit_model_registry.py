"""Report model drift from explicit provider evidence and a secondary catalog.

Run with python -m scripts.audit_model_registry --help. No catalog writes.
"""

import argparse
import json
from datetime import date
from pathlib import Path

from .model_catalog import (
    CatalogInputError,
    add_common_arguments,
    fetch_models_dev,
    incomplete_report,
    read_community,
    read_document,
    read_evidence,
    retirement,
    validate_registry,
)


def audit_registry(registry: dict, evidence: dict, community: dict, provider: str, as_of: date) -> dict:
    validate_registry(registry, provider)
    authoritative = read_evidence(evidence, provider, as_of)
    secondary = read_community(community, provider)
    report = {
        "schema_version": 1,
        "provider": provider,
        "as_of": as_of.isoformat(),
        "complete": True,
        "source_url": evidence["source_url"],
        "observed_on": evidence["observed_on"],
        "confirmed_deprecations": [],
        "capability_drift": [],
        "review_needed": [],
        "candidates": [],
    }
    local = {entry["model_name"].lower(): entry for entry in registry["models"]}
    for name, entry in sorted(local.items()):
        proof = authoritative.get(name)
        if proof is None:
            report["review_needed"].append({"model": entry["model_name"], "reason": "absent_from_provider_snapshot"})
        else:
            retired = retirement(proof, as_of)
            if retired:
                report["confirmed_deprecations"].append({"model": entry["model_name"], "status": retired})
            elif "expires_on" in proof:
                report["review_needed"].append(
                    {"model": entry["model_name"], "reason": "scheduled_expiration", "expires_on": proof["expires_on"]}
                )
            changes = {
                field: {"local": entry.get(field), "observed": value}
                for field, value in sorted(proof.get("capabilities", {}).items())
                if field in entry and entry[field] != value
            }
            for field in sorted(proof.get("capabilities", {}).keys() - entry.keys()):
                report["review_needed"].append(
                    {"model": entry["model_name"], "reason": "capability_not_declared", "field": field}
                )
            if changes:
                report["capability_drift"].append(
                    {"model": entry["model_name"], "source": "provider_snapshot", "changes": changes}
                )
            if not {"context_window", "max_output_tokens"}.issubset(proof.get("capabilities", {})):
                report["review_needed"].append({"model": entry["model_name"], "reason": "provider_capacities_missing"})
        record = secondary.get(name)
        if record is None:
            report["review_needed"].append({"model": entry["model_name"], "reason": "absent_from_models_dev"})
            continue
        if record.get("status"):
            report["review_needed"].append(
                {"model": entry["model_name"], "reason": "models_dev_status", "status": record["status"]}
            )
        limits = record.get("limit", {})
        if not {"context", "output"}.issubset(limits):
            report["review_needed"].append({"model": entry["model_name"], "reason": "models_dev_limits_missing"})
        changes = {
            field: {"local": entry.get(field), "observed": limits[key]}
            for key, field in [("context", "context_window"), ("output", "max_output_tokens")]
            if key in limits and entry.get(field) != limits[key]
        }
        if changes:
            report["capability_drift"].append(
                {"model": entry["model_name"], "source": "models_dev_review_needed", "changes": changes}
            )
    for name in sorted((authoritative.keys() | secondary.keys()) - local.keys()):
        proof = authoritative.get(name)
        report["candidates"].append(
            {
                "model": proof["id"] if proof else secondary[name]["id"],
                "evidence": "provider_snapshot" if proof else "community_only_unverified",
                "status": (retirement(proof, as_of) if proof else None) or "review_needed",
            }
        )
    report["status"] = (
        "review_needed"
        if any(report[key] for key in ("confirmed_deprecations", "capability_drift", "review_needed", "candidates"))
        else "clean"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--models-dev", type=Path, help="Frozen api.json path")
    source.add_argument("--fetch-models-dev", action="store_true", help="Fetch the public api.json explicitly")
    args = parser.parse_args(argv)
    try:
        community = fetch_models_dev() if args.fetch_models_dev else read_document(args.models_dev)
        report = audit_registry(
            read_document(args.registry), read_document(args.evidence), community, args.provider, args.as_of
        )
    except CatalogInputError as exc:
        report = incomplete_report(exc)
    print(json.dumps(report, indent=2, sort_keys=True))
    return {"clean": 0, "review_needed": 1, "incomplete": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
