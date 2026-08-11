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
  type LucideIcon,
} from "lucide-react";
import { ThemeSwitch } from "./theme-switch";
import { AgentRail } from "./agent-rail";

type NavigationItem = { href: string; label: string; key: string; icon: LucideIcon };

const navigation: NavigationItem[] = [
  { href: "/", label: "Overview", icon: LayoutDashboard, key: "overview" },
  { href: "/research", label: "Research", icon: BookOpenText, key: "research" },
  { href: "/scorecards", label: "Scorecards", icon: ListChecks, key: "scorecards" },
  { href: "/agenda", label: "Catalyst Agenda", icon: CalendarCheck, key: "agenda" },
  { href: "/monitors", label: "Monitors", icon: Radar, key: "monitors" },
  { href: "/capabilities", label: "Capabilities", icon: Blocks, key: "capabilities" },
  { href: "/portfolio", label: "Portfolio", icon: BriefcaseBusiness, key: "portfolio" },
  { href: "/retro", label: "Trade Retro", icon: History, key: "retro" },
  { href: "/operations", label: "Operations", icon: SlidersHorizontal, key: "operations" },
];

const SIDEBAR_STORAGE_KEY = "trading-partner-sidebar-collapsed";
const AGENT_RAIL_STORAGE_KEY = "trading-partner-agent-rail-collapsed";

export function ConsoleShell({
  active,
  title,
  eyebrow,
  children,
}: {
  active: string;
  title: string;
  eyebrow: string;
  children: ReactNode;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const [agentRailCollapsed, setAgentRailCollapsed] = useState(false);
  const [overlayViewport, setOverlayViewport] = useState(false);

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

  return (
    <div className={`app-shell${collapsed ? " sidebar-collapsed" : ""}${agentRailCollapsed ? " agent-rail-collapsed" : ""}`}>
      <aside className="sidebar" id="console-navigation-panel">
        <div className="sidebar-header">
          <Link className="brand" href="/" aria-label="Trading Partner console home">
            <img
              alt=""
              className="brand-logo"
              height="40"
              src="/assets/trading-partner-brand/logo.png"
              width="40"
            />
            <span className="brand-copy">
              <strong>Trading Partner</strong>
              <small>LOCAL CONTROL ROOM</small>
            </span>
          </Link>
          <button
            aria-label={collapsed ? "Expand navigation" : "Collapse navigation"}
            aria-expanded={!collapsed}
            className="sidebar-toggle"
            onClick={toggleSidebar}
            type="button"
          >
            {collapsed ? <PanelLeftOpen aria-hidden="true" /> : <PanelLeftClose aria-hidden="true" />}
          </button>
        </div>
        <nav className="nav-list" aria-label="Console navigation">
          {navigation.map((item) => {
            const Icon = item.icon;
            return (
              <Link
                aria-label={item.label}
                className={item.key === active ? "nav-item active" : "nav-item"}
                data-label={item.label}
                href={item.href}
                key={item.key}
              >
                <Icon aria-hidden="true" className="nav-icon" strokeWidth={1.7} />
                <span className="nav-label">{item.label}</span>
              </Link>
            );
          })}
        </nav>
        <div className="sidebar-bottom">
          <ThemeSwitch />
          <div className="sidebar-foot">
            <span className="pulse-dot" />
            <div className="sidebar-foot-copy">
              <strong>Loopback only</strong>
              <small>127.0.0.1 · gated actions</small>
            </div>
          </div>
        </div>
      </aside>
      <button
        aria-label="Close open side panel"
        className={`workspace-pane-backdrop${overlayOpen ? " visible" : ""}`}
        onClick={() => {
          applySidebarCollapsed(true);
          applyAgentRailCollapsed(true);
        }}
        tabIndex={-1}
        type="button"
      />
      <main className="main-content">
        <header className="page-header">
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
          <div className="page-heading">
            <p className="eyebrow">{eyebrow}</p>
            <h1>{title}</h1>
          </div>
          <div className="page-header-actions">
            <div className="environment-chip">
              <span className="pulse-dot" /> DEVELOPMENT
            </div>
            <button
              aria-controls="console-agent-panel"
              aria-expanded={!agentRailCollapsed}
              aria-label={agentRailCollapsed ? "Open Agent panel" : "Close Agent panel"}
              className={`workspace-pane-toggle right${agentRailCollapsed ? " collapsed" : ""}`}
              onClick={toggleAgentRail}
              title={`${agentRailCollapsed ? "Open" : "Close"} Agent · ⌘⇧A`}
              type="button"
            >
              {agentRailCollapsed ? <PanelRightOpen aria-hidden="true" /> : <PanelRightClose aria-hidden="true" />}
            </button>
          </div>
        </header>
        {children}
      </main>
      <AgentRail collapsed={agentRailCollapsed} onCollapsedChange={applyAgentRailCollapsed} />
    </div>
  );
}
