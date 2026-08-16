"use client";

import { useEffect } from "react";

export type AgentPageContext = {
  surface: string;
  selected_subject_id?: string | null;
  selected_monitor_id?: string | null;
  selected_run_id?: string | null;
  active_tab?: string | null;
  workbench_subject_id?: string | null;
};

let currentContext: AgentPageContext | null = null;
let currentSignature = "";

/**
 * Register navigation-only context for the shared Agent rail. Page data never
 * belongs here: the Agent must read durable/current facts through capabilities.
 */
export function useAgentPageContext(context: AgentPageContext): void {
  const signature = JSON.stringify(context);
  useEffect(() => {
    currentContext = context;
    currentSignature = signature;
    return () => {
      if (currentSignature === signature) {
        currentContext = null;
        currentSignature = "";
      }
    };
  }, [signature]);
}

export function getAgentPageContext(): AgentPageContext | null {
  return currentContext ? { ...currentContext } : null;
}
