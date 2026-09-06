import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";

// The Python server serves ONE file and does a string replace of
// __RUN_DATA__ in it. Building to a single inlined HTML keeps that contract
// exactly, so serve.py needs no change: no static directory, no MIME
// handling, and `uv run sarathi serve` stays self-contained with no Node on
// the box.
export default defineConfig({
  plugins: [react(), viteSingleFile()],
  build: {
    target: "es2020",
    cssCodeSplit: false,
    assetsInlineLimit: 100_000_000,
    reportCompressedSize: false,
  },
  server: {
    // `uv run sarathi serve` on 8420; vite on 5173 proxies the telemetry.
    proxy: { "/ws": { target: "ws://127.0.0.1:8420", ws: true } },
  },
});
