# Host setup

```toml
[mcp_servers.trading-partner]
command = "uv"
args = ["run", "trading-partner-mcp"]
```

Other unattended trading, options/complex orders, short selling, order replacement,
and autonomous confirmation remain out of scope.

Installed hosts must use the explicit `runtime.env` produced by
`trading-partner-init`. Mutable files belong below its `RUNTIME_ROOT`; private
Observation bodies and real account-basis checkpoints must never enter Git, package
data, examples, tests, documentation, or tool output.
