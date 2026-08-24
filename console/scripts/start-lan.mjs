import { spawn } from "node:child_process";
import { lstatSync, readFileSync } from "node:fs";
import { networkInterfaces } from "node:os";

const mode = process.argv[2];
if (mode !== "dev" && mode !== "start") {
  console.error("Usage: node scripts/start-lan.mjs dev|start");
  process.exit(2);
}

function passwordFromOwnerFile(path) {
  if (!path) return "";
  const metadata = lstatSync(path);
  if (!metadata.isFile() || metadata.isSymbolicLink()) {
    throw new Error("LAN password file must be a regular, non-symlink file.");
  }
  if ((metadata.mode & 0o077) !== 0) {
    throw new Error("LAN password file must not be accessible by group or other users.");
  }
  return readFileSync(path, "utf8").trim();
}

let password = process.env.TRADING_PARTNER_CONSOLE_LAN_PASSWORD ?? "";
if (!password) {
  try {
    password = passwordFromOwnerFile(
      process.env.TRADING_PARTNER_CONSOLE_LAN_PASSWORD_FILE ?? "",
    );
  } catch (error) {
    console.error(error instanceof Error ? error.message : "Unable to read LAN password file.");
    process.exit(2);
  }
}
if (password.length < 1) {
  console.error("TRADING_PARTNER_CONSOLE_LAN_PASSWORD must not be empty.");
  console.error("Set it directly or use an owner-only TRADING_PARTNER_CONSOLE_LAN_PASSWORD_FILE.");
  console.error("Never place the password in NEXT_PUBLIC_* or a URL.");
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
      TRADING_PARTNER_CONSOLE_LAN_PASSWORD: password,
    },
  },
);

for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => child.kill(signal));
child.on("exit", (code, signal) => {
  process.exit(code ?? (signal ? 0 : 1));
});
