# MCP host setup

This guide connects an installed Trading Partner core runtime to Claude Desktop,
Cursor, or another local stdio MCP host. It uses absolute paths because desktop
applications do not necessarily inherit your interactive shell `PATH`.

## 1. Install and initialize

Install the tagged core package with [uv](https://docs.astral.sh/uv/concepts/tools/):

```bash
uv tool install --python 3.13 \
  "git+https://github.com/KarlZhu-ZXC/trading-partner.git@v0.6.0"
trading-partner-init --json
```

The second command creates an owner-only runtime directory, writes `runtime.env`,
applies every packaged database migration, and returns a secret-safe JSON receipt.
Copy these two values from that receipt:

```json
{
  "mcp_command": "/ABSOLUTE/PATH/trading-partner-mcp",
  "mcp_args": ["--env-file", "/ABSOLUTE/PATH/runtime.env"]
}
```

Do not replace the absolute paths with `~`, environment-variable syntax, or a path
from another machine. Do not paste API keys directly into MCP host JSON. Optional
Provider keys belong in the generated owner-only `runtime.env`.
The generated file also pins `RUNTIME_ROOT` to its own directory. Every mutable
token, lock, attachment, backup, Observation inbox, and account-basis checkpoint
therefore survives tool upgrades outside the installed Wheel or virtual environment.

Running `trading-partner-init` again preserves the existing file and idempotently
upgrades its database. It never downloads market data or contacts a broker.

## 2. Claude Desktop

In Claude Desktop, open **Settings → Developer → Edit Config**. The manual local
server file is normally:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Merge this entry into the existing `mcpServers` object:

```json
{
  "mcpServers": {
    "trading-partner": {
      "command": "/ABSOLUTE/PATH/trading-partner-mcp",
      "args": [
        "--env-file",
        "/ABSOLUTE/PATH/runtime.env"
      ]
    }
  }
}
```

Save valid JSON, completely quit Claude Desktop, and reopen it. Open the connector
manager and confirm that `trading-partner` and its tools are visible. Claude now
also supports `.mcpb` desktop extensions, but Trading Partner does not publish one
yet; this guide documents the tested stdio configuration.

If startup fails, inspect Claude MCP logs:

- macOS: `~/Library/Logs/Claude`
- Windows: `%APPDATA%\Claude\logs`

## 3. Cursor

Cursor reads either:

- project configuration: `.cursor/mcp.json` in one workspace; or
- global configuration: `~/.cursor/mcp.json` for all local workspaces.

Use the same stdio entry:

```json
{
  "mcpServers": {
    "trading-partner": {
      "command": "/ABSOLUTE/PATH/trading-partner-mcp",
      "args": [
        "--env-file",
        "/ABSOLUTE/PATH/runtime.env"
      ]
    }
  }
}
```

Open **Cursor Settings → Tools & MCP** and verify the server after saving the file.
Reload the Cursor window if the edited entry is not detected. Cursor Agent CLI users
can additionally run:

```bash
cursor-agent mcp list
cursor-agent mcp list-tools trading-partner
```

The stdio process runs on the machine where Cursor executes the MCP configuration.
A remote SSH workspace may therefore require the installed command and runtime file
on that remote machine; do not copy broker credentials merely to make a remote agent
work.

## 4. Generic stdio host

Any host that accepts the common `mcpServers` command/args shape can use the same
entry. If the host uses a different settings schema, preserve the exact process
contract:

```text
/ABSOLUTE/PATH/trading-partner-mcp \
  --env-file /ABSOLUTE/PATH/runtime.env
```

The host owns process start, stop, and restart. Restart or reload that host after
changing its MCP configuration. Do not run a second manual copy of the stdio server
in a terminal: two processes cannot share one host stdio stream.

## 5. Verify the connection

Ask the host:

> Call Trading Partner `system_health`. Report `mcp_surface_profile`,
> `public_tool_count`, `surface_schema_version`, operational health, and the Data
> Quality summary. Do not refresh any Provider or broker.

A successful result proves that the MCP process, schema, and local database are
available. It does **not** prove that every optional external Provider is configured
or reachable. Configuration-only checks and live probes remain separately labelled.

Then try one safe, non-writing question:

> Resolve `TSLA`, retrieve the latest available quote with provenance and freshness,
> and explain any warning. Do not create a Research Subject or refresh an account.

## Upgrade and uninstall

Upgrade the installed tool to a newer tag, then apply its packaged migrations:

```bash
uv tool install --python 3.13 --force \
  "git+https://github.com/KarlZhu-ZXC/trading-partner.git@NEW_TAG"
trading-partner-init
```

Uninstalling the executable deliberately preserves the runtime directory and its
database:

```bash
uv tool uninstall trading-partner
```

Back up or remove the `runtime_home` printed by `trading-partner-init --json`
separately. Never delete it as part of an automated tool upgrade.

## Source-checkout alternative

Contributors and users of the optional local Console can keep the repository-based
workflow. The command must use an absolute checkout path:

```json
{
  "mcpServers": {
    "trading-partner": {
      "command": "/ABSOLUTE/PATH/uv",
      "args": [
        "--directory",
        "/ABSOLUTE/PATH/trading-partner",
        "run",
        "trading-partner-mcp"
      ]
    }
  }
}
```

The source process reads only the project-root `.env`. Editing code or `.env` does
not update an already running MCP process; reload or restart the host once.
