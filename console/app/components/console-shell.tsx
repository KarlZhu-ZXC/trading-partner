"use client";

import type { ReactNode } from "react";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Blocks,
  BookOpenText,
  BriefcaseBusiness,
  CalendarCheck,
  LayoutDashboard,
  History,
  ListChecks,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Radar,
  SlidersHorizontal,
  LogOut,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import { ThemeSwitch } from "./theme-switch";
import { AgentRail } from "./agent-rail";
import { GlobalNotifications } from "./global-notifications";

export const CONSOLE_PAGE_LABELS = {
  overview: "Overview",
  "decision-workbench": "Workbench",
  research: "Research",
  scorecards: "Scorecards",
  agenda: "Catalyst Agenda",
  monitors: "Monitors",
  capabilities: "Capabilities",
  portfolio: "Portfolio",
  retro: "Trade Retro",
  operations: "Operations",
  chat: "Agent Chat",
} as const;

type ConsolePageKey = keyof typeof CONSOLE_PAGE_LABELS;
type NavigationItem = { href: string; key: Exclude<ConsolePageKey, "chat">; icon: LucideIcon };

const navigation: NavigationItem[] = [
  { href: "/", icon: LayoutDashboard, key: "overview" },
  { href: "/decision-workbench", icon: Workflow, key: "decision-workbench" },
  { href: "/research", icon: BookOpenText, key: "research" },
  { href: "/scorecards", icon: ListChecks, key: "scorecards" },
  { href: "/agenda", icon: CalendarCheck, key: "agenda" },
  { href: "/monitors", icon: Radar, key: "monitors" },
  { href: "/capabilities", icon: Blocks, key: "capabilities" },
  { href: "/portfolio", icon: BriefcaseBusiness, key: "portfolio" },
  { href: "/retro", icon: History, key: "retro" },
  { href: "/operations", icon: SlidersHorizontal, key: "operations" },
];

const SIDEBAR_STORAGE_KEY = "trading-partner-sidebar-collapsed";
const AGENT_RAIL_STORAGE_KEY = "trading-partner-agent-rail-collapsed";

export function ConsoleShell({
  active,
  children,
  pageActions,
}: {
  active: ConsolePageKey;
  children: ReactNode;
  pageActions?: ReactNode;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const [agentRailCollapsed, setAgentRailCollapsed] = useState(false);
  const [overlayViewport, setOverlayViewport] = useState(false);
  const [lanMode, setLanMode] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/lan-auth", { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        const payload = (await response.json()) as { enabled?: unknown };
        setLanMode(payload.enabled === true);
      })
      .catch(() => setLanMode(false));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    try {
      const storedCollapsed = window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === "true";
      const storedAgentCollapsed = window.localStorage.getItem(AGENT_RAIL_STORAGE_KEY) === "true";
      const overlayViewport = window.matchMedia("(max-width: 1100px)").matches;
      document.documentElement.classList.toggle("sidebar-collapsed", overlayViewport || storedCollapsed);
      document.documentElement.classList.toggle(
        "agent-rail-collapsed",
        overlayViewport || storedAgentCollapsed,
      );
      setCollapsed(overlayViewport || storedCollapsed);
      setAgentRailCollapsed(overlayViewport || storedAgentCollapsed);
      setOverlayViewport(overlayViewport);
    } catch {
      // The expanded default remains usable when storage is unavailable.
    }
  }, []);

  const applySidebarCollapsed = useCallback((next: boolean) => {
    setCollapsed(next);
    document.documentElement.classList.toggle("sidebar-collapsed", next);
    try {
      window.localStorage.setItem(SIDEBAR_STORAGE_KEY, String(next));
    } catch {
      // The current-session selection remains usable when storage is unavailable.
    }
  }, []);

  const applyAgentRailCollapsed = useCallback((next: boolean) => {
    setAgentRailCollapsed(next);
    document.documentElement.classList.toggle("agent-rail-collapsed", next);
    try {
      window.localStorage.setItem(AGENT_RAIL_STORAGE_KEY, String(next));
    } catch {
      // The current-session selection remains usable when storage is unavailable.
    }
  }, []);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 1100px)");
    function handleViewportChange(event: MediaQueryListEvent) {
      setOverlayViewport(event.matches);
      if (event.matches) {
        applySidebarCollapsed(true);
        applyAgentRailCollapsed(true);
      }
    }
    media.addEventListener("change", handleViewportChange);
    return () => media.removeEventListener("change", handleViewportChange);
  }, [applyAgentRailCollapsed, applySidebarCollapsed]);

  useEffect(() => {
    function handleKeyDown(event: globalThis.KeyboardEvent) {
      const modifier = event.metaKey || event.ctrlKey;
      if (modifier && event.shiftKey && event.key.toLowerCase() === "l") {
        event.preventDefault();
        setCollapsed((current) => {
          const next = !current;
          if (overlayViewport && !next) applyAgentRailCollapsed(true);
          document.documentElement.classList.toggle("sidebar-collapsed", next);
          try { window.localStorage.setItem(SIDEBAR_STORAGE_KEY, String(next)); } catch { /* noop */ }
          return next;
        });
        return;
      }
      if (modifier && event.shiftKey && event.key.toLowerCase() === "a") {
        event.preventDefault();
        setAgentRailCollapsed((current) => {
          const next = !current;
          if (overlayViewport && !next) applySidebarCollapsed(true);
          document.documentElement.classList.toggle("agent-rail-collapsed", next);
          try { window.localStorage.setItem(AGENT_RAIL_STORAGE_KEY, String(next)); } catch { /* noop */ }
          return next;
        });
        return;
      }
      if (event.key === "Escape" && window.matchMedia("(max-width: 1100px)").matches) {
        applySidebarCollapsed(true);
        applyAgentRailCollapsed(true);
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [applyAgentRailCollapsed, applySidebarCollapsed, overlayViewport]);

  useEffect(() => {
    if (!overlayViewport || (collapsed && agentRailCollapsed)) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = previousOverflow; };
  }, [agentRailCollapsed, collapsed, overlayViewport]);

  function toggleSidebar() {
    const next = !collapsed;
    if (overlayViewport && !next) applyAgentRailCollapsed(true);
    applySidebarCollapsed(next);
  }

  function toggleAgentRail() {
    const next = !agentRailCollapsed;
    if (overlayViewport && !next) applySidebarCollapsed(true);
    applyAgentRailCollapsed(next);
  }

  const overlayOpen = overlayViewport && (!collapsed || !agentRailCollapsed);

  async function signOutLanSession() {
    try {
      await fetch("/api/lan-auth", { method: "DELETE" });
    } catch {
      // The cookie clears on redirect anyway; surface nothing extra here.
    }
    window.location.assign("/lan-login");
  }

  return (
    <div className={`app-shell${collapsed ? " sidebar-collapsed" : ""}${agentRailCollapsed ? " agent-rail-collapsed" : ""}`}>
      <aside className="sidebar" id="console-navigation-panel">
        <div className="sidebar-header">
          <Link className="brand" href="/" aria-label="Trading Partner Console Home">
            <img
              alt=""
              className="brand-logo"
              height="40"
              src="/assets/trading-partner-brand/logo.png"
              width="40"
            />
            <span className="brand-copy">
              <strong>Trading Partner</strong>
              <small>LOCAL HUB</small>
            </span>
          </Link>
        </div>
        <nav className="nav-list" aria-label="Console Navigation">
          {navigation.map((item) => {
            const Icon = item.icon;
            const label = CONSOLE_PAGE_LABELS[item.key];
            return (
              <Link
                aria-label={label}
                className={item.key === active ? "nav-item active" : "nav-item"}
                data-label={label}
                href={item.href}
                key={item.key}
              >
                <Icon aria-hidden="true" className="nav-icon" strokeWidth={1.7} />
                <span className="nav-label">{label}</span>
              </Link>
            );
          })}
        </nav>
        <div className="sidebar-bottom">
          {lanMode ? (
            <button className="lan-sign-out" onClick={signOutLanSession} type="button">
              <LogOut aria-hidden="true" />
              <span className="theme-switch-label">Sign Out LAN Session</span>
            </button>
          ) : null}
          <div className="sidebar-foot">
            <span className="pulse-dot" />
            <div className="sidebar-foot-copy">
              <strong>{lanMode ? "Trusted LAN" : "Loopback only"}</strong>
              <small>{lanMode ? "AUTHENTICATED · API LOCAL" : "127.0.0.1 · GATED ACTIONS"}</small>
            </div>
          </div>
        </div>
      </aside>
      <button
        aria-label="Close Open Side Panel"
        className={`workspace-pane-backdrop${overlayOpen ? " visible" : ""}`}
        onClick={() => {
          applySidebarCollapsed(true);
          applyAgentRailCollapsed(true);
        }}
        tabIndex={-1}
        type="button"
      />
      <main className="main-content">
        <GlobalNotifications />
        <header className="global-header">
          <button
            aria-controls="console-navigation-panel"
            aria-expanded={!collapsed}
            aria-label={collapsed ? "Open navigation panel" : "Close navigation panel"}
            className={`workspace-pane-toggle left${collapsed ? " collapsed" : ""}`}
            onClick={toggleSidebar}
            title={`${collapsed ? "Open" : "Close"} navigation · ⌘⇧L`}
            type="button"
          >
            {collapsed ? <PanelLeftOpen aria-hidden="true" /> : <PanelLeftClose aria-hidden="true" />}
          </button>
          <div className="global-header-actions">
            <div className="environment-chip">
              <span className="pulse-dot" /> {lanMode ? "LAN SESSION" : "DEVELOPMENT"}
            </div>
            <ThemeSwitch />
            <button
              aria-controls="console-agent-panel"
              aria-expanded={!agentRailCollapsed}
              aria-label={agentRailCollapsed ? "Open Agent Panel" : "Close Agent Panel"}
              className={`workspace-pane-toggle right${agentRailCollapsed ? " collapsed" : ""}`}
              onClick={toggleAgentRail}
              title={`${agentRailCollapsed ? "Open" : "Close"} Agent · ⌘⇧A`}
              type="button"
            >
              {agentRailCollapsed ? <PanelRightOpen aria-hidden="true" /> : <PanelRightClose aria-hidden="true" />}
            </button>
          </div>
        </header>
        <header className="page-header">
          <div className="page-heading"><h1>{CONSOLE_PAGE_LABELS[active]}</h1></div>
          {pageActions ? <div className="page-level-actions">{pageActions}</div> : null}
        </header>
        {children}
      </main>
      <AgentRail
        collapsed={agentRailCollapsed}
        onCollapsedChange={applyAgentRailCollapsed}
        overlayViewport={overlayViewport}
      />
    </div>
  );
}
