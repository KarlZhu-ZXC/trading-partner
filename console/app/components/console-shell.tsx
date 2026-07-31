import type { ReactNode } from "react";
import Link from "next/link";
import { ThemeSwitch } from "./theme-switch";

const navigation = [
  { href: "/", label: "总览", mark: "OV", key: "overview" },
  { href: "/monitors", label: "Monitor", mark: "MN", key: "monitors" },
  { href: "/capabilities", label: "能力目录", mark: "CP", key: "capabilities" },
  { href: "/portfolio", label: "账户", mark: "PF", key: "portfolio" },
  { href: "/operations", label: "操作中心", mark: "AC", key: "operations" },
];

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
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link className="brand" href="/" aria-label="Trading Partner 控制台首页">
          <span className="brand-symbol">TP</span>
          <span>
            <strong>Trading Partner</strong>
            <small>LOCAL CONTROL ROOM</small>
          </span>
        </Link>
        <nav className="nav-list" aria-label="控制台导航">
          {navigation.map((item) => (
            <Link
              aria-label={item.label}
              className={item.key === active ? "nav-item active" : "nav-item"}
              href={item.href}
              key={item.key}
            >
              <span className="nav-mark">{item.mark}</span>
              <span>{item.label}</span>
            </Link>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <ThemeSwitch />
          <div className="sidebar-foot">
            <span className="pulse-dot" />
            <div>
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
