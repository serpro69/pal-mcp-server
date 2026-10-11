"""Frozen-input audit/generator contracts; no provider credentials or network calls."""

import copy
import json
import subprocess
import sys
from datetime import date
from http.client import IncompleteRead
from io import BytesIO
from pathlib import Path
from urllib.error import URLError

import pytest

from scripts import audit_model_registry as audit
from scripts import generate_model_entries as generator
from scripts import model_catalog as catalog

FIXTURES = Path(__file__).parent / "fixtures" / "model_catalog"
AS_OF = date(2026, 10, 11)


@pytest.fixture
def snapshots():
    return [catalog.read_document(FIXTURES / name) for name in ("registry.json", "provider.json", "models-dev.json")]


def test_audit_distinguishes_provider_retirement_and_secondary_review(snapshots):
    registry, evidence, community = snapshots
    report = audit.audit_registry(registry, evidence, community, "openai", AS_OF)
    assert report["status"] == "review_needed"
    assert report["confirmed_deprecations"] == [
        {"model": "expired", "status": "expired"},
        {"model": "old", "status": "deprecated"},
    ]
    assert {"model": "community-missing", "reason": "absent_from_models_dev"} in report["review_needed"]
    assert {"model": "current:tag", "reason": "models_dev_status", "status": "deprecated"} in report["review_needed"]
    assert report["capability_drift"] == [
        {
            "model": "current:tag",
            "source": "provider_snapshot",
            "changes": {
                "context_window": {"local": 100000, "observed": 200000},
                "supports_temperature": {"local": True, "observed": False},
            },
        }
    ]
    assert {"model": "community-only", "evidence": "community_only_unverified", "status": "review_needed"} in report[
        "candidates"
    ]


def test_generation_is_deterministic_idempotent_hidden_and_nonmutating(snapshots):
    registry, evidence, _ = snapshots
    before = copy.deepcopy(registry)
    first = generator.generate_entries(registry, evidence, "openai", AS_OF)
    assert registry == before
    assert first == generator.generate_entries(registry, evidence, "openai", AS_OF)
    assert first["candidate_ids"] == ["new:for"]
    assert first["registry"]["models"][:-1] == registry["models"]
    candidate = first["registry"]["models"][-1]
    assert candidate == {"model_name": "new:for", "enabled_by_default": False, **evidence["models"][4]["capabilities"]}
    assert not {"aliases", "intelligence_score", "capability_rank"} & candidate.keys()
    assert '+      "enabled_by_default": false' in first["diff"]
    assert first["skipped"] == [
        {"model": "alias-collision", "reason": "conflicts_with_existing_alias"},
        {"model": "insufficient", "reason": "verified_capacities_missing"},
        {"model": "retired-candidate", "reason": "deprecated_or_expiring"},
    ]
    second = generator.generate_entries(first["registry"], evidence, "openai", AS_OF)
    assert second["diff"] == ""
    assert second["candidate_ids"] == []
    assert second["registry"] == first["registry"]


@pytest.mark.parametrize("mutation", ["unknown_field", "duplicate_alias", "cycle", "bad_bool"])
def test_invalid_local_registry_is_not_a_clean_audit(snapshots, mutation):
    registry, evidence, community = snapshots
    if mutation == "unknown_field":
        registry["models"][0]["invented_capability"] = True
    elif mutation == "duplicate_alias":
        registry["models"][1]["aliases"].append("current")
    elif mutation == "cycle":
        registry["models"][0]["aliases"].append("old")
        registry["models"][1]["aliases"].append("current:tag")
    else:
        registry["models"][0]["enabled_by_default"] = "false"
    with pytest.raises(catalog.CatalogInputError):
        audit.audit_registry(registry, evidence, community, "openai", AS_OF)


@pytest.mark.parametrize(
    "mutation",
    [
        "partial",
        "wrong_provider",
        "future",
        "unknown_field",
        "duplicate",
        "rank",
        "bad_capacity",
        "bad_status",
        "community_authority",
    ],
)
def test_provider_evidence_requires_explicit_valid_provenance(snapshots, mutation):
    registry, evidence, _ = snapshots
    if mutation == "partial":
        evidence["complete"] = False
    elif mutation == "wrong_provider":
        evidence["provider"] = "google"
    elif mutation == "future":
        evidence["observed_on"] = "2027-01-01"
    elif mutation == "unknown_field":
        evidence["models"][0]["guess"] = True
    elif mutation == "duplicate":
        evidence["models"].append(evidence["models"][0])
    elif mutation == "rank":
        evidence["models"][0]["capabilities"]["intelligence_score"] = 20
    elif mutation == "bad_capacity":
        evidence["models"][0]["capabilities"]["context_window"] = True
    elif mutation == "bad_status":
        evidence["models"][0]["status"] = []
    else:
        evidence["source_url"] = "https://models.dev/api.json"
    with pytest.raises(catalog.CatalogInputError):
        generator.generate_entries(registry, evidence, "openai", AS_OF)


@pytest.mark.parametrize(
    "mutation", ["missing_provider", "empty_models", "partial_entry", "unknown_status", "bad_status", "bad_limit"]
)
def test_incomplete_community_cannot_pass_clean(snapshots, mutation):
    registry, evidence, community = snapshots
    models = community["openai"]["models"]
    if mutation == "missing_provider":
        community.clear()
    elif mutation == "empty_models":
        models.clear()
    elif mutation == "partial_entry":
        del models["old"]["id"]
    elif mutation == "unknown_status":
        models["old"]["status"] = "expired"
    elif mutation == "bad_status":
        models["old"]["status"] = []
    else:
        models["old"]["limit"]["output"] = -1
    with pytest.raises(catalog.CatalogInputError):
        audit.audit_registry(registry, evidence, community, "openai", AS_OF)


def test_future_expiration_is_review_needed_and_missing_provider_is_not_retirement(snapshots):
    registry, evidence, community = snapshots
    evidence["models"][2]["expires_on"] = "2026-11-01"
    evidence["models"] = [entry for entry in evidence["models"] if entry["id"] != "old"]
    report = audit.audit_registry(registry, evidence, community, "openai", AS_OF)
    assert report["confirmed_deprecations"] == []
    assert {"model": "expired", "reason": "scheduled_expiration", "expires_on": "2026-11-01"} in report["review_needed"]
    assert {"model": "old", "reason": "absent_from_provider_snapshot"} in report["review_needed"]


@pytest.mark.parametrize("failure", [URLError("offline"), TimeoutError("timeout"), IncompleteRead(b'{"partial":')])
def test_network_failure_emits_incomplete_report(monkeypatch, capsys, failure):
    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(catalog, "urlopen", fail)
    assert (
        audit.main(
            [
                "--registry",
                str(FIXTURES / "registry.json"),
                "--provider",
                "openai",
                "--evidence",
                str(FIXTURES / "provider.json"),
                "--as-of",
                AS_OF.isoformat(),
                "--fetch-models-dev",
            ]
        )
        == 2
    )
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "incomplete" and report["complete"] is False


@pytest.mark.parametrize("raw", [b'{"models": [], "models": []}', b'{"partial":', b"[]"])
def test_malformed_json_is_rejected(raw):
    with pytest.raises(catalog.CatalogInputError):
        catalog.decode_document(raw)


def test_fetch_has_timeout_and_actual_byte_limit(monkeypatch):
    seen = []
    monkeypatch.setattr(catalog, "MAX_INPUT_BYTES", 8)

    def fake_open(url, timeout):
        seen.append((url, timeout))
        return BytesIO(b" " * 20)

    monkeypatch.setattr(catalog, "urlopen", fake_open)
    with pytest.raises(catalog.CatalogInputError, match="limit"):
        catalog.fetch_models_dev()
    assert seen == [(catalog.MODELS_DEV_URL, 15)]


def test_cli_exit_status_output_and_exclusive_proposal(tmp_path):
    common = [
        "--registry",
        str(FIXTURES / "registry.json"),
        "--provider",
        "openai",
        "--evidence",
        str(FIXTURES / "provider.json"),
        "--as-of",
        AS_OF.isoformat(),
    ]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.audit_model_registry",
            *common,
            "--models-dev",
            str(FIXTURES / "models-dev.json"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1, result.stderr
    assert json.loads(result.stdout)["status"] == "review_needed"
    proposal = tmp_path / "proposal.json"
    command = [sys.executable, "-m", "scripts.generate_model_entries", *common, "--output", str(proposal)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    original = proposal.read_bytes()
    assert json.loads(result.stdout)["candidate_ids"] == ["new:for"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode == 2
    assert proposal.read_bytes() == original


def test_clean_report_requires_matching_evidence_and_community(snapshots):
    registry, evidence, community = snapshots
    registry["models"] = [registry["models"][0]]
    proof = evidence["models"][0]
    proof["capabilities"] = {
        key: registry["models"][0][key] for key in ("context_window", "max_output_tokens", "supports_temperature")
    }
    evidence["models"] = [proof]
    community["openai"]["models"] = {"current:tag": {"id": "current:tag", "limit": {"context": 100000, "output": 8000}}}
    report = audit.audit_registry(registry, evidence, community, "openai", AS_OF)
    assert report["status"] == "clean"
    proof["capabilities"] = {}
    assert audit.audit_registry(registry, evidence, community, "openai", AS_OF)["status"] == "review_needed"


@pytest.mark.parametrize("provider", ["google", "openai", "azure", "xai", "dial", "custom", "openrouter"])
def test_generation_uses_each_providers_schema(snapshots, provider):
    registry, evidence, _ = snapshots
    evidence["provider"] = provider
    if provider == "azure":
        for entry in registry["models"]:
            entry["deployment"] = entry["model_name"]
    report = generator.generate_entries(registry, evidence, provider, AS_OF)
    if provider == "azure":
        assert report["candidate_ids"] == []
        assert {"model": "new:for", "reason": "requires_operator_deployment_mapping"} in report["skipped"]
    else:
        assert report["candidate_ids"] == ["new:for"]


def test_candidate_with_inconsistent_capacities_is_skipped(snapshots):
    registry, evidence, _ = snapshots
    evidence["models"][4]["capabilities"]["max_output_tokens"] = 200000
    report = generator.generate_entries(registry, evidence, "openai", AS_OF)
    assert report["candidate_ids"] == []
    assert {"model": "new:for", "reason": "output_exceeds_context"} in report["skipped"]


def test_omitted_runtime_default_is_verification_work_not_capability_drift(snapshots):
    registry, evidence, community = snapshots
    registry["models"] = [registry["models"][0]]
    del registry["models"][0]["supports_temperature"]
    proof = evidence["models"][0]
    proof["capabilities"] = {"context_window": 100000, "max_output_tokens": 8000, "supports_temperature": True}
    evidence["models"] = [proof]
    community["openai"]["models"] = {"current:tag": {"id": "current:tag", "limit": {"context": 100000, "output": 8000}}}
    report = audit.audit_registry(registry, evidence, community, "openai", AS_OF)
    assert report["capability_drift"] == []
    assert report["review_needed"] == [
        {"model": "current:tag", "reason": "capability_not_declared", "field": "supports_temperature"}
    ]
