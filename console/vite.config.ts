import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const target = process.env.MAILGUARD_API_URL || "http://127.0.0.1:8000";

export default defineConfig({
  base: "./",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
