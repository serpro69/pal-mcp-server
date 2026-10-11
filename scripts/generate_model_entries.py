"""Propose hidden model entries from reviewed evidence; never edit the source registry.

Run with python -m scripts.generate_model_entries --help. Standard output includes a review diff.
"""

import argparse
import json
from datetime import date
from difflib import unified_diff

from .model_catalog import (
    CatalogInputError,
    add_common_arguments,
    incomplete_report,
    read_document,
    read_evidence,
    retirement,
    validate_registry,
)


def generate_entries(registry: dict, evidence: dict, provider: str, as_of: date) -> dict:
    configs = validate_registry(registry, provider)
    records = read_evidence(evidence, provider, as_of)
    canonical = {name.lower() for name in configs}
    occupied = canonical | {alias.lower() for config in configs.values() for alias in config.aliases}
    candidates, skipped = [], []
    for name, record in sorted(records.items()):
        if name in canonical:
            continue
        reason = None
        capabilities = record.get("capabilities", {})
        if name in occupied:
            reason = "conflicts_with_existing_alias"
        elif retirement(record, as_of) or "expires_on" in record:
            reason = "deprecated_or_expiring"
        elif provider == "azure":
            reason = "requires_operator_deployment_mapping"
        elif any(
            type(capabilities.get(key)) is not int or capabilities[key] <= 0
            for key in ("context_window", "max_output_tokens")
        ):
            reason = "verified_capacities_missing"
        elif capabilities["max_output_tokens"] > capabilities["context_window"]:
            reason = "output_exceeds_context"
        if reason:
            skipped.append({"model": record["id"], "reason": reason})
            continue
        candidates.append(
            {"model_name": record["id"], "enabled_by_default": False, **dict(sorted(capabilities.items()))}
        )
    proposal = {**registry, "models": [*registry["models"], *candidates]}
    validate_registry(proposal, provider)
    before = json.dumps(registry, indent=2, ensure_ascii=False) + "\n"
    after = json.dumps(proposal, indent=2, ensure_ascii=False) + "\n"
    diff = "".join(
        unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="registry.json",
            tofile="candidate-registry.json",
        )
    )
    return {
        "schema_version": 1,
        "status": "proposal",
        "provider": provider,
        "source_url": evidence["source_url"],
        "observed_on": evidence["observed_on"],
        "as_of": as_of.isoformat(),
        "candidate_ids": [entry["model_name"] for entry in candidates],
        "skipped": skipped,
        "diff": diff,
        "registry": proposal,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    parser.add_argument("--output", type=str, help="New proposal file, created exclusively; never overwrites")
    args = parser.parse_args(argv)
    try:
        report = generate_entries(read_document(args.registry), read_document(args.evidence), args.provider, args.as_of)
        if args.output:
            try:
                with open(args.output, "x", encoding="utf-8") as stream:
                    stream.write(json.dumps(report["registry"], indent=2, ensure_ascii=False) + "\n")
            except OSError as exc:
                raise CatalogInputError("Unable to create proposal file; use a new writable output path") from exc
    except CatalogInputError as exc:
        print(json.dumps(incomplete_report(exc), indent=2, sort_keys=True))
        return 2
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
