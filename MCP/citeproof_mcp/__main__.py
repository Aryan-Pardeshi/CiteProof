# SPDX-License-Identifier: MIT
"""Run the server over stdio: `python -m citeproof_mcp`."""

from citeproof_mcp.server import build_server


def main() -> None:
    build_server().run("stdio")


if __name__ == "__main__":
    main()
