import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// `npm run dev` serves the page on Vite's own server (with instant reload) and passes the API and
// file requests on to the Rust server. The Rust server only answers to its own address and only
// takes changes from its own page, so the proxy presents the requests as coming from there: Host
// (changeOrigin) and Origin.
const server = "http://127.0.0.1:8080";
const toServer = { target: server, changeOrigin: true, headers: { origin: server } };

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": toServer, "/files": toServer },
  },
  test: { environment: "node" },
});
