import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Trading Partner · Local Hub",
  description: "Trading Partner local operations, investment research, and shared Agent console.",
};

const appearanceInitScript = `
  try {
    var storedTheme = localStorage.getItem("trading-partner-theme");
    document.documentElement.dataset.theme = storedTheme === "dark" ? "dark" : "light";
  } catch (_) {
    document.documentElement.dataset.theme = "light";
  }

  try {
    var overlayViewport = window.matchMedia("(max-width: 1100px)").matches;
    var sidebarCollapsed = overlayViewport || localStorage.getItem("trading-partner-sidebar-collapsed") === "true";
    document.documentElement.classList.toggle("sidebar-collapsed", sidebarCollapsed);
  } catch (_) {
    document.documentElement.classList.remove("sidebar-collapsed");
  }

  try {
    var agentRailCollapsed = overlayViewport || localStorage.getItem("trading-partner-agent-rail-collapsed") === "true";
    document.documentElement.classList.toggle("agent-rail-collapsed", agentRailCollapsed);
    var storedAgentRailWidth = Number(localStorage.getItem("trading-partner-agent-rail-width"));
    if (Number.isFinite(storedAgentRailWidth) && storedAgentRailWidth >= 320 && storedAgentRailWidth <= 720) {
      document.documentElement.style.setProperty("--agent-rail-user-width", storedAgentRailWidth + "px");
    }
  } catch (_) {
    document.documentElement.classList.remove("agent-rail-collapsed");
  }
`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html data-theme="light" lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: appearanceInitScript }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
