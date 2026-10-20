# SPDX-License-Identifier: MIT
"""Copy the CiteProof MCP server into a folder that Claude Science's sandbox can read.

Claude Science runs a "Local command" connector inside a Windows sandbox that can only
read the connector's own workspace folder and its bundled runtime, not a project's
virtual environment. This script writes a self-contained copy plus a ``run.py`` that its
bundled ``python3`` (which already has ``mcp``, ``pydantic`` and ``httpx``) can start:

    python scripts/build_claude_science_bundle.py <destination-folder>

See docs/CLAUDE_SCIENCE.md for the full install steps. Re-run this script after every
update of the server, then reconnect the connector.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]

RUN_PY = '''# SPDX-License-Identifier: MIT
"""Start the CiteProof MCP server over stdio from any working directory."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from citeproof_mcp.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main()
'''


# Everything a bundle contains. An existing destination is cleared only when it holds
# nothing else, so a wrong path (for example the connector folder that also holds the
# review data) is refused instead of deleted.
BUNDLE_ENTRIES = ("run.py", "citeproof_mcp", "LICENSES", "NOTICE.md")


def _clear_previous_bundle(dest: Path) -> None:
    if not dest.exists():
        return
    if not dest.is_dir():
        raise SystemExit(f"destination is not a folder: {dest}")
    foreign = sorted(child.name for child in dest.iterdir() if child.name not in BUNDLE_ENTRIES)
    if foreign:
        raise SystemExit(
            f"refusing to use {dest}: it holds files that are not part of a bundle ({', '.join(foreign[:5])}). "
            "Pass an empty folder or a previous bundle folder, for example <workspace>/bundle."
        )
    for child in dest.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def build(dest: Path) -> None:
    dest = dest.resolve()
    if dest == PACKAGE_ROOT or PACKAGE_ROOT in dest.parents:
        raise SystemExit("destination must be outside the MCP folder")
    _clear_previous_bundle(dest)
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        PACKAGE_ROOT / "citeproof_mcp",
        dest / "citeproof_mcp",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copytree(PACKAGE_ROOT / "LICENSES", dest / "LICENSES")
    shutil.copy2(PACKAGE_ROOT / "NOTICE.md", dest / "NOTICE.md")
    (dest / "run.py").write_text(RUN_PY, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python scripts/build_claude_science_bundle.py <destination-folder>")
    build(Path(sys.argv[1]))
    print(f"bundle written to {Path(sys.argv[1]).resolve()}")
