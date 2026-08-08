import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Trading Partner · Local Control Room",
  description: "无内置聊天模型的 Trading Partner 本地操作与投资研究控制台。",
};

const appearanceInitScript = `
  try {
    var storedTheme = localStorage.getItem("trading-partner-theme");
    document.documentElement.dataset.theme = storedTheme === "dark" ? "dark" : "light";
  } catch (_) {
    document.documentElement.dataset.theme = "light";
  }

  try {
    var sidebarCollapsed = localStorage.getItem("trading-partner-sidebar-collapsed") === "true";
    document.documentElement.classList.toggle("sidebar-collapsed", sidebarCollapsed);
  } catch (_) {
    document.documentElement.classList.remove("sidebar-collapsed");
  }
`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html data-theme="light" lang="zh-CN" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: appearanceInitScript }} />
      </head>
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        {children}
      </body>
    </html>
  );
}
