"use client";
import { useState } from "react";
import { Search } from "lucide-react";
import { Button, IconButton, LinkButton, Input, Select, Textarea, Tag, FilterChip, SelectableRow, Table } from "../components/ui/controls";
import { Badge, Card, FormField, ConfirmationDialog, DescriptionList, Empty, ErrorNote, HorizontalTabs } from "../components/ui";
import { MultiSelectAutosuggest } from "../components/multi-select-autosuggest";
import { ThemeSwitch } from "../components/theme-switch";
import galleryStyles from "../components/ui/preview.module.css";

export function ComponentGallery() {
  const [open, setOpen] = useState(false); const [tab, setTab] = useState("one"); const [selected, setSelected] = useState<string[]>([]);
  return <main className={galleryStyles.library}><header><h1>Component Library</h1><p>One visual system. Every state, both themes.</p></header><ThemeSwitch />
    <Card title="Actions & Navigation"><div className={galleryStyles.row}><Button variant="primary" onClick={() => setOpen(true)}>Review Decision</Button><Button>Refresh Sources</Button><Button variant="danger">Archive</Button><Button disabled>Unavailable</Button><Button busy>Save</Button><IconButton aria-label="Search"><Search /></IconButton><LinkButton href="/decision-workbench#notes">Open Notes</LinkButton></div></Card>
    <Card title="Fields & Filters"><div className={galleryStyles.grid}><FormField label="Search Notes"><Input placeholder="Title or summary" /></FormField><FormField label="Period"><Select><option>All History</option><option>Last 30 Days</option></Select></FormField><MultiSelectAutosuggest label="Account" placeholder="All Accounts" value={selected} onChange={setSelected} options={[{ value: "ira", label: "Schwab IRA" }, { value: "brokerage", label: "Schwab Brokerage" }]} /><FormField label="Review Date" required><Input type="date" required /></FormField><FormField label="Invalid Value"><Input aria-invalid="true" defaultValue="Review required" /></FormField><FormField label="Unavailable"><Input disabled value="No source" readOnly /></FormField></div><FormField label="Review Notes"><Textarea placeholder="Record the evidence behind your judgment…" /></FormField></Card>
    <Card title="Status, Classification & Selection"><div className={galleryStyles.row}><Badge value="SUCCEEDED" /><Badge value="DEGRADED" /><Badge value="FAILED" /><Badge value="PENDING" /><Tag>Long-Term Investment</Tag><FilterChip label="Schwab IRA" onRemove={() => setSelected([])}>Schwab IRA</FilterChip></div><div className={galleryStyles.stack}><SelectableRow selected><strong>AAPL · Schwab IRA</strong><p>Same Instrument, exact account identity.</p></SelectableRow><SelectableRow><strong>AAPL · Schwab Brokerage</strong><p>Independent activity and cost basis.</p></SelectableRow></div></Card>
    <Card title="Information"><DescriptionList items={[{ label: "Account", value: "Schwab IRA" }, { label: "Quality", value: <Badge value="DEGRADED" /> }, { label: "Cycles", value: "12" }, { label: "Currency", value: "USD" }]} /><Table><thead><tr><th>Instrument</th><th>Account</th><th>Status</th></tr></thead><tbody><tr><td>AAPL</td><td>Schwab IRA</td><td><Badge value="OPEN" /></td></tr></tbody></Table></Card>
    <HorizontalTabs items={[{ id: "one", label: "Overview" }, { id: "two", label: "Behavior" }]} value={tab} onChange={setTab} ariaLabel="Sample Sections" idPrefix="sample-tab" panelIdPrefix="sample-panel" /><section id={`sample-panel-${tab}`} role="tabpanel" aria-labelledby={`sample-tab-${tab}`}><Empty>No activity matches this scope.</Empty><ErrorNote role="alert">The source is unavailable. Existing evidence is retained.</ErrorNote></section>
    <ConfirmationDialog open={open} title="Review Sample Decision" description="Synthetic example. No business record will be created." onConfirm={() => setOpen(false)} onCancel={() => setOpen(false)}><FormField label="Review Note"><Textarea /></FormField></ConfirmationDialog>
  </main>;
}
