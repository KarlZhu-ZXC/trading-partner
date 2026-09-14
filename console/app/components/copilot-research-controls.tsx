"use client";

import { Disclosure, FormField, Input, Select } from "./ui";
import type { CopilotResearchConfig } from "../lib/agent-api";

export function CopilotResearchControls({ config, disabled, onChange }: {
  config: CopilotResearchConfig; disabled: boolean; onChange: (config: CopilotResearchConfig) => void;
}) {
  return <>
              <FormField label="Copilot Mode"><Select aria-label="Copilot Mode" value={config.research_mode} disabled={disabled} onChange={(event) => onChange({ ...config, research_mode: event.target.value as CopilotResearchConfig["research_mode"] })}><option value="standard">Chat</option><option value="research">Research</option><option value="challenge">Counter-review</option></Select></FormField>
              {config.research_mode !== "standard" && <Disclosure title="Research Budget" variant="compact"><p>Read-only research stops at the first budget reached. Counter-review uses the selected model within this budget.</p><FormField label="Maximum Seconds" required><Input aria-label="Maximum Seconds" disabled={disabled} type="number" min={30} max={600} required value={config.research_max_seconds} onChange={(event) => onChange({ ...config, research_max_seconds: Number(event.target.value) })} /></FormField><FormField label="Maximum Model Calls" required><Input aria-label="Maximum Model Calls" disabled={disabled} type="number" min={1} max={16} required value={config.research_max_model_calls} onChange={(event) => onChange({ ...config, research_max_model_calls: Number(event.target.value) })} /></FormField><FormField label="Maximum Tool Calls" required><Input aria-label="Maximum Tool Calls" disabled={disabled} type="number" min={1} max={48} required value={config.research_max_tool_calls} onChange={(event) => onChange({ ...config, research_max_tool_calls: Number(event.target.value) })} /></FormField></Disclosure>}
  </>;
}
