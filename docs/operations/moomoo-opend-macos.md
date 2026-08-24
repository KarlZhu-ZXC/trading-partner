# Moomoo command-line OpenD on macOS

Trading Partner uses the official `moomoo-api` SDK through one local command-line
OpenD gateway. OpenD remains external infrastructure: its executable, login
configuration, runtime state, and logs are never committed to this repository.

## Local layout

```text
/Applications/OpenD.app/    # searchable GUI application bundle
/Applications/OpenD.xml -> $HOME/.trading-partner-opend/OpenD.xml

$HOME/Library/Application Support/TradingPartner/
├── OpenD/
│   └── OpenD.xml            # mode 0600; may contain login configuration
└── logs/                    # mode 0700; files created as 0600

$HOME/.trading-partner-opend -> Application Support/TradingPartner/OpenD
$HOME/Library/LaunchAgents/com.trading-partner.moomoo-opend.plist
```

The no-space symlink is used only for OpenD's `-cfg_file` parser. API access is
loopback-only on `127.0.0.1:11111`. The LaunchAgent remains registered with
`RunAtLoad=false`, `KeepAlive=false`, and `Umask=0077`; OpenD's internal monitor
is disabled. Moomoo-backed providers check the endpoint before opening an SDK
context and start this LaunchAgent on demand when the local port is unavailable.

## Operations

```bash
# Status
launchctl print "gui/$(id -u)/com.trading-partner.moomoo-opend"
lsof -nP -iTCP:11111 -sTCP:LISTEN

# Start or restart while keeping the task registered
launchctl kickstart -k "gui/$(id -u)/com.trading-partner.moomoo-opend"

# Stop/unload
launchctl bootout "gui/$(id -u)/com.trading-partner.moomoo-opend"

# Load again after a manual bootout
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/com.trading-partner.moomoo-opend.plist"
```

Listening on port 11111 is only transport readiness. The first Moomoo-backed
provider call starts OpenD if needed, then waits for `get_global_state()` to report
both quote and trade login before creating its SDK context. After a restart, OpenD
may need several additional seconds to restore those sessions. Operational checks
must also require the expected server version before declaring it ready.

## Security and upgrades

- Never commit `OpenD.xml`, copy it into project `.env`, or put login credentials
  in LaunchAgent arguments.
- Keep the entire `$HOME/.com.moomoo.OpenD` runtime directory owner-only because
  provider logs can include account or request metadata.
- Upgrade the command-line OpenD and `moomoo-api` SDK together. A version mismatch
  can leave basic calls working while newer protocols return `Unknown protocol ID`.
- First-device login, device-lock, SMS, or graphical verification can still require
  manual intervention. Trading Partner remains read-only and never unlocks trading.
