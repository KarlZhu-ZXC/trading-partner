"use client";

import { useEffect, useState } from "react";

type Theme = "light" | "dark";

const STORAGE_KEY = "trading-partner-theme";

export function ThemeSwitch() {
  const [theme, setTheme] = useState<Theme>("light");

  useEffect(() => {
    const current = document.documentElement.dataset.theme;
    setTheme(current === "dark" ? "dark" : "light");
  }, []);

  function selectTheme(nextTheme: Theme) {
    document.documentElement.dataset.theme = nextTheme;
    document.documentElement.style.colorScheme = nextTheme;
    try {
      window.localStorage.setItem(STORAGE_KEY, nextTheme);
    } catch {
      // Theme selection still applies when local storage is unavailable.
    }
    setTheme(nextTheme);
  }

  return (
    <div className="theme-switch" role="group" aria-label="界面主题">
      <button
        aria-label="浅色"
        aria-pressed={theme === "light"}
        className={theme === "light" ? "active" : ""}
        onClick={() => selectTheme("light")}
        type="button"
      >
        <span aria-hidden="true">☀</span>
        <span>浅色</span>
      </button>
      <button
        aria-label="深色"
        aria-pressed={theme === "dark"}
        className={theme === "dark" ? "active" : ""}
        onClick={() => selectTheme("dark")}
        type="button"
      >
        <span aria-hidden="true">◐</span>
        <span>深色</span>
      </button>
    </div>
  );
}
