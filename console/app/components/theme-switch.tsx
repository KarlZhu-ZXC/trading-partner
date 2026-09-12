"use client";

import { useEffect, useState } from "react";
import { Button } from "./ui/controls";
import themeStyles from "./ui/theme-switch.module.css";
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
    <div className={themeStyles.root} role="group" aria-label="Theme">
      <Button size="sm"
        aria-label="Light Theme"
        aria-pressed={theme === "light"}
        variant={theme === "light" ? "primary" : "secondary"}
        onClick={() => selectTheme("light")}
        type="button"
      >
        <Sun aria-hidden="true" className={themeStyles.icon} strokeWidth={1.7} />
        <span className={themeStyles.label}>Light</span>
      </Button>
      <Button size="sm"
        aria-label="Dark Theme"
        aria-pressed={theme === "dark"}
        variant={theme === "dark" ? "primary" : "secondary"}
        onClick={() => selectTheme("dark")}
        type="button"
      >
        <Moon aria-hidden="true" className={themeStyles.icon} strokeWidth={1.7} />
        <span className={themeStyles.label}>Dark</span>
      </Button>
    </div>
  );
}
