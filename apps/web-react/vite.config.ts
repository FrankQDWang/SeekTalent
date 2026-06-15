import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const backendHost = process.env.SEEKTALENT_DEV_BACKEND_HOST ?? "127.0.0.1";
const backendPort = process.env.SEEKTALENT_DEV_BACKEND_PORT ?? "8012";
const frontendPort = Number(process.env.SEEKTALENT_DEV_FRONTEND_PORT ?? "5178");

export default defineConfig({
  plugins: [react()],
  server: {
    port: frontendPort,
    strictPort: true,
    proxy: {
      "/api": `http://${backendHost}:${backendPort}`,
    },
  },
  build: {
    assetsDir: "_app",
    outDir: "dist",
    sourcemap: false,
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    setupFiles: ["./src/test/setup.ts"],
    expect: { requireAssertions: true },
  },
});
