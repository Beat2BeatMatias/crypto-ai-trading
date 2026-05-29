import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 3100,
    proxy: {
      "/api": "http://localhost:8100",
      "/ws": { target: "ws://localhost:8100", ws: true },
    },
  },
});
