import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server on 5173; /api proxied to the FastAPI backend on :8000 so we never
// hardcode the backend origin in fetch calls (see src/api/client.ts).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
