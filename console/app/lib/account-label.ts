export type AccountAliases = Record<string, string>;

export function accountLabel(account: Record<string, unknown>, aliases: AccountAliases = {}): string {
  const reference = typeof account.account_ref === "string" ? account.account_ref : "";
  if (Object.hasOwn(aliases, reference) && aliases[reference]?.trim()) return aliases[reference];
  const provider = String(account.provider ?? reference.split("_")[0] ?? "Account").toLowerCase();
  const name = provider === "schwab" ? "Schwab" : provider === "moomoo" ? "Moomoo" : provider.toUpperCase() || "Account";
  return reference ? `${name} · ${reference.slice(-6).toUpperCase()}` : `${name} · Unavailable`;
}
