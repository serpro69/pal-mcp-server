"""Admission and rendering share a finite allowance and actual file accounting."""

import json
from types import SimpleNamespace

import pytest

from providers.shared import ModelCapabilities, ProviderType
from tools.chat import ChatTool
from tools.shared.exceptions import ToolExecutionError
from utils.file_utils import FileReadReport, check_total_file_size, read_file_content, read_files
from utils.model_context import FileBudget, ModelContext


def model_context(context_window=40000, ceiling=4000):
    caps = ModelCapabilities(
        provider=ProviderType.CUSTOM,
        model_name="test",
        friendly_name="Test",
        context_window=context_window,
        max_output_tokens=ceiling,
    )
    return ModelContext("test", provider=SimpleNamespace(get_capabilities=lambda name: caps))


@pytest.mark.parametrize("remaining", [None, 10000, 2500, 0])
def test_gate_and_embedder_use_identical_remaining_allowance(tmp_path, remaining):
    path = tmp_path / "file.py"
    path.write_text("print('hello')\n" * 100)
    context = model_context()
    gate = check_total_file_size([str(path)], "test", model_context=context, remaining_budget=remaining)
    tool = ChatTool()
    tool._model_context = context
    tool._current_arguments = {"_remaining_tokens": remaining}
    if gate:
        with pytest.raises(ToolExecutionError) as caught:
            tool._prepare_file_content_for_prompt([str(path)], None)
        output = json.loads(caught.value.payload)
        assert output["metadata"]["limit"] == gate["metadata"]["limit"]
        assert output["metadata"]["overage"] > 0
        assert output["metadata"]["largest_files"][0]["path"] == str(path)
        assert output["metadata"]["response_tokens"] == 4000
    else:
        content, included = tool._prepare_file_content_for_prompt([str(path)], None)
        assert included == [str(path)]
        assert "print('hello')" in content
    assert tool._file_context_report["budget"] == context.file_budget(remaining).limit
    if remaining == 0:
        assert tool._file_context_report["budget"] == 0


def test_rendered_headers_and_line_numbers_count_at_gate(tmp_path):
    path = tmp_path / "tiny.py"
    path.write_text("x\n" * 30)
    context = model_context()
    raw_tokens = context.estimate_tokens(path.read_text())
    budget = FileBudget(raw_tokens + 1, "file_allocation", 100, 100, 4000)
    error = check_total_file_size([str(path)], "test", model_context=context, budget=budget, include_line_numbers=True)
    assert error is not None
    assert error["metadata"]["total_estimated_tokens"] > raw_tokens


def test_reader_reports_only_content_actually_included(tmp_path, monkeypatch):
    from utils import file_utils

    small = tmp_path / "small.py"
    small.write_text("CONTENT_SENTINEL")
    large = tmp_path / "large.py"
    large.write_text("huge " * 5000)
    alias = tmp_path / "alias.py"
    alias.symlink_to(small)
    missing = tmp_path / "missing.py"
    denied = tmp_path / "denied.py"
    real_validate = file_utils.resolve_and_validate_path

    def validate(path):
        if str(path) == str(denied):
            raise PermissionError("synthetic denial")
        return real_validate(path)

    monkeypatch.setattr(file_utils, "resolve_and_validate_path", validate)
    context = model_context()
    report = FileReadReport()
    content = read_files(
        [str(small), str(alias), str(large), str(missing), str(denied)],
        max_tokens=700,
        reserve_tokens=0,
        report=report,
        token_estimator=context.estimate_tokens,
    )
    assert report.included == [str(small)]
    assert report.omitted == [str(large)]
    assert set(report.unavailable) == {str(missing), str(denied)}
    assert content.count("CONTENT_SENTINEL") == 1
    assert "SKIPPED FILES" in content
    assert report.rendered_tokens == context.estimate_tokens(content) <= 700


def test_direct_code_uses_budget_once(tmp_path):
    path = tmp_path / "source.txt"
    path.write_text("file content")
    file_content, _ = read_file_content(str(path), include_line_numbers=False)
    code = "code " * 500
    expected = f"\n--- BEGIN DIRECT CODE ---\n{code}\n--- END DIRECT CODE ---\n\n\n{file_content}"
    context = model_context()
    report = FileReadReport()
    content = read_files(
        [str(path)],
        code=code,
        max_tokens=context.estimate_tokens(expected) + 4,
        reserve_tokens=0,
        report=report,
        token_estimator=context.estimate_tokens,
    )
    assert code in content and "file content" in content
    assert report.included == [str(path)]


def test_invalid_budget_cannot_fall_back_to_large_allowance(tmp_path):
    path = tmp_path / "x.py"
    path.write_text("hello")
    tool = ChatTool()
    tool._model_context = model_context(ceiling=0)
    tool._current_arguments = {}
    with pytest.raises(ValueError, match="max_output_tokens"):
        tool._prepare_file_content_for_prompt([str(path)], None)
