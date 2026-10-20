# Use CiteProof MCP in Claude Science

This page shows how to add the CiteProof MCP server to Claude Science as a **Local command** connector. It was tested on Claude Science 0.1.67 on Windows 11 with the native sandbox. Other platforms may differ; if you try them, please open an issue.

## Contents

- [Why the install is not one line](#why-the-install-is-not-one-line)
- [Steps](#steps)
- [Update after a new release](#update-after-a-new-release)
- [Where your review data lives](#where-your-review-data-lives)
- [Troubleshooting](#troubleshooting)
- [Other MCP clients](#other-mcp-clients)

## Why the install is not one line

Claude Science runs a Local command connector inside a Windows sandbox. The sandbox can read the connector's own workspace folder and Claude Science's bundled runtime. It cannot read a project folder or a Python virtual environment somewhere else on your disk. A command that points at your own `.venv` fails with "interpreter not readable inside the sandbox".

So we copy the server into the connector's workspace folder and start it with Claude Science's own `python3`, which already has `mcp`, `pydantic` and `httpx`.

We do not ask you to run `claude-science sandbox setup`. That command changes Windows folder permissions, and you do not need it for this install.

## Steps

1. **Add the connector once so Claude Science creates its workspace folder.** Open Customize, then Connectors, then Add connector, then Local command. Use the name `citeproof` (lowercase letters, digits and hyphens only) and the command line `python3 placeholder.py`. It will show an error. That is expected. On our machine the workspace folder appeared after the first start attempt of a connector. If it does not appear on yours, create the folder from step 2 by hand.
2. **Find the workspace folder.** On Windows it is:

   ```text
   C:/Users/<you>/.claude-science/orgs/<org-id>/workspaces/_mcp-citeproof
   ```

   `claude-science logs` also prints it as `cwd` in the sandbox policy lines for `_mcp-citeproof`.
3. **Build the bundle into that folder.** From the root of this repository:

   ```bash
   python MCP/scripts/build_claude_science_bundle.py "C:/Users/<you>/.claude-science/orgs/<org-id>/workspaces/_mcp-citeproof/bundle"
   ```

   The script needs only the Python standard library.
4. **Point the connector at the bundle.** Remove the placeholder connector and add it again with:

   - Name: `citeproof`
   - Command line (use forward slashes; backslashes are stripped by the form):

     ```text
     python3 C:/Users/<you>/.claude-science/orgs/<org-id>/workspaces/_mcp-citeproof/bundle/run.py
     ```

   - Advanced settings, environment variables:

     ```text
     CITEPROOF_MCP_HOME=C:/Users/<you>/.claude-science/orgs/<org-id>/workspaces/_mcp-citeproof/data
     ```

5. **Check the tool list.** The connector page should list 24 tools, from `review_create` to `export_bundle`. By default Claude Science asks for approval each time a tool runs. You can set each tool to always allow once you trust it.
6. **Start a review.** Ask Claude Science to create a review with `review_create`, then follow the order in [SPEC.md](SPEC.md#workflow). `review_status` tells the agent what to do next at any point.

## Update after a new release

Run the build script again with the same destination, then reconnect:

```bash
python MCP/scripts/build_claude_science_bundle.py "<the same bundle folder>"
```

The running server keeps the old code in memory. The Connectors page has no restart button when the connector is healthy, so remove the connector and add it again with the same settings. Your review data is in the `data` folder and survives this.

## Where your review data lives

One SQLite file per review under `CITEPROOF_MCP_HOME/reviews/`. Nothing is sent to us. The only network calls are DOI checks to Crossref and doi.org when the agent calls `verify_dois`.

The sandbox only lets the connector write inside its own workspace folder, which is why the data folder sits there. If you remove the connector, keep the `data` folder, or copy it first.

## Troubleshooting

| Message in the connector page | Cause and fix |
|---|---|
| `this kernel's Python interpreter ... is not readable inside the sandbox` | The command points outside the workspace folder. Use the bundle path from step 4. |
| `Set "command" to node, npx or python3 ...` | You gave an absolute path to a Python that Claude Science does not provide. Use `python3`. |
| `can't open file ... Permission denied` | The bundle is not inside the connector's workspace folder. Build it into `.../workspaces/_mcp-citeproof/bundle`. |
| Name rejected | Names allow lowercase letters, digits and hyphens only. |
| 18 tools instead of 24 | The old server process is still running. Remove and re-add the connector. |

## Other MCP clients

Any client that can start a stdio MCP server works. Install the package and point the client at the `citeproof-mcp` command:

```bash
pip install ./MCP
```

For clients that use a JSON config (Claude Desktop, Cursor and similar), the entry looks like this:

```json
{
  "mcpServers": {
    "citeproof": {
      "command": "citeproof-mcp",
      "env": { "CITEPROOF_MCP_HOME": "/path/to/your/review-data" }
    }
  }
}
```

If you leave `CITEPROOF_MCP_HOME` out, review files go to `~/.citeproof-mcp`.

These clients were not tested for this release. The server follows the standard MCP stdio protocol, and it was tested against both mcp 1.27 and mcp 2.x.
