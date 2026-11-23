"""Admission and rendering share a finite allowance and actual file accounting."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from providers.shared import ModelCapabilities, ProviderType
from tools.chat import ChatTool
from tools.shared.exceptions import ToolExecutionError
from utils.file_utils import (
    FileReadReport,
    bind_file_descriptions,
    check_total_file_size,
    read_file_content,
    read_files,
)
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


def test_descriptions_bind_only_to_eligible_files_with_stable_precedence(tmp_path):
    pkg = tmp_path / "pkg"
    nested = pkg / "sub"
    nested.mkdir(parents=True)
    sibling = tmp_path / "pkg2"
    sibling.mkdir()
    paths = [pkg / "a.py", nested / "b.py", nested / "c.py", nested / "blank.py", sibling / "d.py"]
    for path in paths:
        path.touch()
    alias = tmp_path / "alias"
    alias.symlink_to(nested)
    alias2 = tmp_path / "alias2"
    alias2.symlink_to(nested)
    labels = {
        str(pkg): "all",
        str(alias2): "losing alias",
        str(alias): "nested",
        str(paths[2]): "direct",
        str(alias / "c.py"): "losing direct alias",
        str(paths[3]): "  ",
        "relative": "invalid",
        str(tmp_path / "missing"): "missing",
    }
    expected = dict(zip(map(str, paths[:3]), ("all", "nested", "direct")))
    for mapping in (labels, dict(reversed(labels.items()))):
        assert bind_file_descriptions([str(pkg), str(sibling)], mapping) == expected
    assert bind_file_descriptions([str(paths[0])], {str(paths[1]): "unattached"}) == {}


def test_description_binding_skips_unavailable_child(tmp_path, monkeypatch):
    available = tmp_path / "a.py"
    denied = tmp_path / "b.py"
    available.touch()
    denied.touch()
    is_file = Path.is_file

    def check_file(path):
        if path == denied:
            raise PermissionError("unsearchable symlink target")
        return is_file(path)

    monkeypatch.setattr(Path, "is_file", check_file)
    assert bind_file_descriptions([str(tmp_path)], {str(tmp_path): "context"}) == {str(available): "context"}


def test_description_is_safe_bounded_and_only_on_success(tmp_path):
    path = tmp_path / "content.txt"
    path.write_text("body")
    label = "  a\n\x00\u202eb] ---- c " + "x" * 210
    effective = bind_file_descriptions([str(path)], {str(path): label})[str(path)]
    assert len(effective) == 200 and effective.endswith("…")
    assert effective.startswith("a b) — c ")
    plain = read_file_content(str(path))
    rendered = read_file_content(str(path), description=label)[0]
    assert rendered == plain[0].replace(") ---", f") [{effective}] ---", 1)
    assert read_file_content(str(path), description=effective)[0] == rendered
    for blank in (None, "", " \t "):
        assert read_file_content(str(path), description=blank) == plain
    for file, kwargs in (
        (str(path), {"max_size": 0}),
        (str(tmp_path / "absent"), {}),
        ("relative", {}),
        (str(tmp_path), {}),
        (str(path), {"max_size": -1}),
    ):
        assert read_file_content(file, description=label, **kwargs) == read_file_content(file, **kwargs)


def test_annotation_counts_at_shared_model_gate(tmp_path, monkeypatch):
    path = str(tmp_path / "file.txt")
    Path(path).write_text("content")
    context = model_context()
    tool = ChatTool()
    tool._model_context = context
    plain, _ = tool._prepare_file_content_for_prompt([path], None)
    limit = context.estimate_tokens(plain)
    monkeypatch.setattr(context, "file_budget", lambda *_: FileBudget(limit, "remaining_input", limit, limit, 4000))
    assert tool._prepare_file_content_for_prompt([path], None) == (plain, [path])
    label = "annotated"
    report = FileReadReport()
    read_files(
        [path],
        max_tokens=limit,
        reserve_tokens=0,
        file_descriptions={path: label},
        include_line_numbers=tool.wants_line_numbers_by_default(),
        report=report,
        token_estimator=context.estimate_tokens,
    )
    expected = context.estimate_tokens(plain.replace(") ---", f") [{label}] ---", 1))
    assert report.omitted == [path] and report.required_tokens == expected
    with pytest.raises(ToolExecutionError) as caught:
        tool._prepare_file_content_for_prompt([path], None, file_descriptions={path: label})
    metadata = json.loads(caught.value.payload)["metadata"]
    assert metadata["limit"] == limit
    assert metadata["overage"] == expected - limit > 0
    assert metadata["largest_files"] == [{"path": path, "tokens": expected}]
