import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// `npm run dev` serves the page on Vite's own server (with instant reload) and passes the API and
// file requests on to the Rust server. changeOrigin sends the Rust server's own address as the
// Host header, which is the only kind it answers to.
const server = "http://127.0.0.1:8080";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: server, changeOrigin: true },
      "/files": { target: server, changeOrigin: true },
    },
  },
  test: { environment: "node" },
});
