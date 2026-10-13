"""Explicitly opted-in installed-CLI checks; no PAL provider keys are required."""

import logging

import pytest

from simulator_tests.test_clink_cli_only import LIVE_SELECTOR_ENV, run_live_clink, selected_live_cli


@pytest.mark.integration
def test_clink_provider_free_first_request_and_followup():
    vendor = selected_live_cli()
    if vendor is None:
        pytest.skip(f"Set {LIVE_SELECTOR_ENV}=codex, claude, or gemini to opt in to installed-CLI checks")
    # Once selected, missing executables/authentication, incompatible flags, and
    # failed requests are failures. Role-required summaries may accompany answers.
    run_live_clink(vendor, logging.getLogger(__name__))
