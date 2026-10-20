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


def build(dest: Path) -> None:
    dest = dest.resolve()
    if dest == PACKAGE_ROOT or PACKAGE_ROOT in dest.parents:
        raise SystemExit("destination must be outside the MCP folder")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    shutil.copytree(
        PACKAGE_ROOT / "citeproof_mcp",
        dest / "citeproof_mcp",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (dest / "run.py").write_text(RUN_PY, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python scripts/build_claude_science_bundle.py <destination-folder>")
    build(Path(sys.argv[1]))
    print(f"bundle written to {Path(sys.argv[1]).resolve()}")
