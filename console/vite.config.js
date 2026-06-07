import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
var target = process.env.MAILGUARD_API_URL || "http://127.0.0.1:8000";
export default defineConfig({
    base: "./",
    plugins: [react()],
    server: {
        port: 5173,
        proxy: {
            "/api": {
                target: target,
                changeOrigin: true,
                rewrite: function (path) { return path.replace(/^\/api/, ""); },
            },
        },
    },
});
