import react from "@vitejs/plugin-react";
import type { ProxyOptions } from "vite";
import { defineConfig } from "vitest/config";

// `npm run dev` serves the page on Vite's own server (http://localhost:5173, with instant reload)
// and passes the API and file requests on to the Rust server. The Rust server only answers to its
// own address (so Host is rewritten: changeOrigin) and only takes changes from its own page. So
// requests from *this* dev page are presented as coming from the server's page; any other Origin
// (another site open in the browser) goes on unchanged, and the server refuses it.
const server = "http://127.0.0.1:8080";
const port = 5173;
const devPage = new Set([`http://localhost:${port}`, `http://127.0.0.1:${port}`]);

const toServer: ProxyOptions = {
  target: server,
  changeOrigin: true,
  configure: (proxy) => {
    proxy.on("proxyReq", (proxyReq, req) => {
      if (devPage.has(req.headers.origin ?? "")) proxyReq.setHeader("origin", server);
    });
  },
};

export default defineConfig({
  plugins: [react()],
  server: {
    port,
    strictPort: true, // the rule above depends on the port
    proxy: { "/api": toServer, "/files": toServer },
  },
  test: { environment: "node" },
});
