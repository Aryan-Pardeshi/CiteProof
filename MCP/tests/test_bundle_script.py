# SPDX-License-Identifier: MIT
"""The Claude Science bundle builder never deletes a folder that is not a bundle."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_claude_science_bundle.py"


def _load():
    spec = importlib.util.spec_from_file_location("build_claude_science_bundle", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_creates_bundle_with_licences(tmp_path) -> None:
    module = _load()
    dest = tmp_path / "bundle"
    module.build(dest)
    assert (dest / "run.py").is_file()
    assert (dest / "citeproof_mcp" / "__main__.py").is_file()
    assert (dest / "LICENSES" / "Apache-2.0.txt").is_file()
    assert (dest / "NOTICE.md").is_file()


def test_rebuild_replaces_a_previous_bundle(tmp_path) -> None:
    module = _load()
    dest = tmp_path / "bundle"
    module.build(dest)
    (dest / "citeproof_mcp" / "stale.py").write_text("x", encoding="utf-8")
    module.build(dest)
    assert not (dest / "citeproof_mcp" / "stale.py").exists()
    assert (dest / "run.py").is_file()


def test_refuses_a_folder_that_holds_review_data(tmp_path) -> None:
    module = _load()
    workspace = tmp_path / "_mcp-citeproof"
    (workspace / "data" / "reviews").mkdir(parents=True)
    review_file = workspace / "data" / "reviews" / "rev-1.db"
    review_file.write_bytes(b"data")
    (workspace / "bundle").mkdir()

    with pytest.raises(SystemExit):
        module.build(workspace)

    assert review_file.read_bytes() == b"data"
    assert (workspace / "bundle").is_dir()


def test_refuses_a_file_as_destination(tmp_path) -> None:
    module = _load()
    target = tmp_path / "not-a-folder"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit):
        module.build(target)
    assert target.read_text(encoding="utf-8") == "x"
