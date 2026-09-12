"use client";

import { createContext, useCallback, useContext, type ReactNode } from "react";
import { useApi } from "../lib/api";
import { accountLabel, type AccountAliases } from "../lib/account-label";

const AccountAliasesContext = createContext<AccountAliases>({});
const EMPTY_ALIASES: AccountAliases = {};

export function AccountAliasesProvider({ children }: { children: ReactNode }) {
  const result = useApi<{ aliases: AccountAliases }>("/account-aliases");
  return <AccountAliasesContext.Provider value={result.data?.aliases ?? EMPTY_ALIASES}>{children}</AccountAliasesContext.Provider>;
}

export function useAccountLabel() {
  const aliases = useContext(AccountAliasesContext);
  return useCallback((account: Record<string, unknown>) => accountLabel(account, aliases), [aliases]);
}
