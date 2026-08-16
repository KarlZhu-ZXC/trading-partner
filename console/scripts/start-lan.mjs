import { spawn } from "node:child_process";
import { networkInterfaces } from "node:os";

const mode = process.argv[2];
if (mode !== "dev" && mode !== "start") {
  console.error("Usage: node scripts/start-lan.mjs dev|start");
  process.exit(2);
}

const password = process.env.TRADING_PARTNER_CONSOLE_LAN_PASSWORD ?? "";
if (password.length < 16) {
  console.error("TRADING_PARTNER_CONSOLE_LAN_PASSWORD must be at least 16 characters.");
  console.error("Set it in the current shell; never place it in NEXT_PUBLIC_* or a URL.");
  process.exit(2);
}

const portRaw = process.env.TRADING_PARTNER_CONSOLE_LAN_PORT ?? "3000";
const port = Number(portRaw);
if (!Number.isInteger(port) || port < 1024 || port > 65535) {
  console.error("TRADING_PARTNER_CONSOLE_LAN_PORT must be an integer from 1024 to 65535.");
  process.exit(2);
}

const addresses = Object.values(networkInterfaces())
  .flatMap((entries) => entries ?? [])
  .filter((entry) => entry.family === "IPv4" && !entry.internal)
  .map((entry) => `http://${entry.address}:${port}`);

console.log("Trading Partner Console LAN mode");
console.log("  Backend: 127.0.0.1:8765 (not exposed)");
console.log(`  Frontend: ${addresses.join(", ") || `http://<this-mac-ip>:${port}`}`);
console.log("  Access: password-protected, trusted LAN only");

const child = spawn(
  process.execPath,
  ["node_modules/next/dist/bin/next", mode, "--hostname", "0.0.0.0", "--port", String(port)],
  {
    stdio: "inherit",
    env: {
      ...process.env,
      TRADING_PARTNER_CONSOLE_LAN_ENABLED: "1",
    },
  },
);

for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => child.kill(signal));
child.on("exit", (code, signal) => {
  process.exit(code ?? (signal ? 0 : 1));
});
