import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  distDir: process.env.CONSOLE_BUILD_DIR ?? ".next",
};

export default nextConfig;
