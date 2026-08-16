const LAN_ENABLED_VALUE = "1";

export const LAN_SESSION_COOKIE = "tp_console_lan_session";
export const LAN_SESSION_SECONDS = 12 * 60 * 60;
export const LAN_PASSWORD_MIN_LENGTH = 16;

const encoder = new TextEncoder();

export function lanModeEnabled(): boolean {
  return process.env.TRADING_PARTNER_CONSOLE_LAN_ENABLED === LAN_ENABLED_VALUE;
}

export function lanPassword(): string | null {
  const password = process.env.TRADING_PARTNER_CONSOLE_LAN_PASSWORD;
  return password && password.length >= LAN_PASSWORD_MIN_LENGTH ? password : null;
}

function bytesToBase64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/u, "");
}

async function hmac(value: string, password: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(password),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign("HMAC", key, encoder.encode(value));
  return bytesToBase64Url(new Uint8Array(signature));
}

function constantTimeEqual(left: string, right: string): boolean {
  const leftBytes = encoder.encode(left);
  const rightBytes = encoder.encode(right);
  let difference = leftBytes.length ^ rightBytes.length;
  const length = Math.max(leftBytes.length, rightBytes.length);
  for (let index = 0; index < length; index += 1) {
    difference |= (leftBytes[index] ?? 0) ^ (rightBytes[index] ?? 0);
  }
  return difference === 0;
}

export async function passwordMatches(candidate: string, password: string): Promise<boolean> {
  const [candidateDigest, passwordDigest] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(candidate)),
    crypto.subtle.digest("SHA-256", encoder.encode(password)),
  ]);
  return constantTimeEqual(
    bytesToBase64Url(new Uint8Array(candidateDigest)),
    bytesToBase64Url(new Uint8Array(passwordDigest)),
  );
}

export async function createLanSession(password: string): Promise<string> {
  const expiresAt = Math.floor(Date.now() / 1000) + LAN_SESSION_SECONDS;
  const nonce = bytesToBase64Url(crypto.getRandomValues(new Uint8Array(18)));
  const payload = `${expiresAt}.${nonce}`;
  return `${payload}.${await hmac(payload, password)}`;
}

export async function verifyLanSession(token: string | undefined, password: string): Promise<boolean> {
  if (!token) return false;
  const [expiresAtRaw, nonce, signature, ...extra] = token.split(".");
  if (!expiresAtRaw || !nonce || !signature || extra.length > 0) return false;
  const expiresAt = Number(expiresAtRaw);
  if (!Number.isSafeInteger(expiresAt) || expiresAt <= Math.floor(Date.now() / 1000)) return false;
  const expected = await hmac(`${expiresAtRaw}.${nonce}`, password);
  return constantTimeEqual(signature, expected);
}
