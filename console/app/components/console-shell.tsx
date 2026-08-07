"use client";

import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import Link from "next/link";
import {
  Blocks,
  BookOpenText,
  BriefcaseBusiness,
  LayoutDashboard,
  PanelLeftClose,
  PanelLeftOpen,
  Radar,
  SlidersHorizontal,
  type LucideIcon,
} from "lucide-react";
import { ThemeSwitch } from "./theme-switch";

type NavigationItem = { href: string; label: string; key: string; icon: LucideIcon };

const navigation: NavigationItem[] = [
  { href: "/", label: "Overview", icon: LayoutDashboard, key: "overview" },
  { href: "/monitors", label: "Monitors", icon: Radar, key: "monitors" },
  { href: "/research", label: "Research", icon: BookOpenText, key: "research" },
  { href: "/capabilities", label: "Capabilities", icon: Blocks, key: "capabilities" },
  { href: "/portfolio", label: "Portfolio", icon: BriefcaseBusiness, key: "portfolio" },
  { href: "/operations", label: "Operations", icon: SlidersHorizontal, key: "operations" },
];

const SIDEBAR_STORAGE_KEY = "trading-partner-sidebar-collapsed";

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

  useEffect(() => {
    try {
      setCollapsed(window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === "true");
    } catch {
      // The expanded default remains usable when storage is unavailable.
    }
  }, []);

  function toggleSidebar() {
    setCollapsed((current) => {
      const next = !current;
      try {
        window.localStorage.setItem(SIDEBAR_STORAGE_KEY, String(next));
      } catch {
        // Width selection still applies for the current session.
      }
      return next;
    });
  }

  return (
    <div className={`app-shell${collapsed ? " sidebar-collapsed" : ""}`}>
      <aside className="sidebar">
        <div className="sidebar-header">
          <Link className="brand" href="/" aria-label="Trading Partner 控制台首页">
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
        <nav className="nav-list" aria-label="控制台导航">
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
      <main className="main-content">
        <header className="page-header">
          <div>
            <p className="eyebrow">{eyebrow}</p>
            <h1>{title}</h1>
          </div>
          <div className="environment-chip">
            <span className="pulse-dot" /> DEVELOPMENT
          </div>
        </header>
        {children}
      </main>
    </div>
  );
}
