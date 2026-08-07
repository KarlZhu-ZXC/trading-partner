"use client";

import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";

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
    <div className="theme-switch" role="group" aria-label="Theme">
      <button
        aria-label="Light theme"
        aria-pressed={theme === "light"}
        className={theme === "light" ? "active" : ""}
        onClick={() => selectTheme("light")}
        type="button"
      >
        <Sun aria-hidden="true" className="theme-icon" strokeWidth={1.7} />
        <span className="theme-switch-label">Light</span>
      </button>
      <button
        aria-label="Dark theme"
        aria-pressed={theme === "dark"}
        className={theme === "dark" ? "active" : ""}
        onClick={() => selectTheme("dark")}
        type="button"
      >
        <Moon aria-hidden="true" className="theme-icon" strokeWidth={1.7} />
        <span className="theme-switch-label">Dark</span>
      </button>
    </div>
  );
}
