"use client";

import { useEffect, useState } from "react";
import { LinkButton, Select } from "./ui/controls";
import previewStyles from "./ui/preview.module.css";

export function DesignPreviewNotice() {
  const [state, setState] = useState("normal");
  useEffect(() => { setState(new URLSearchParams(window.location.search).get("preview_state") ?? "normal"); }, []);
  if (process.env.NEXT_PUBLIC_CONSOLE_DESIGN_PREVIEW !== "1") return null;
  return <aside className={previewStyles.notice} aria-label="Design Preview">
    <div><strong>DESIGN PREVIEW</strong><span>Synthetic data · Operational writes disabled</span></div>
    <label>Sample State<Select value={state} onChange={(event) => { const url = new URL(window.location.href); url.searchParams.set("preview_state", event.target.value); window.location.assign(url); }}><option value="normal">Populated</option><option value="empty">Empty</option><option value="error">Read Error</option></Select></label>
    <LinkButton href="/decision-workbench">Journal</LinkButton><LinkButton href="/design-system">Component Library</LinkButton>
  </aside>;
}
