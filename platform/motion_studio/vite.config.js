import { defineConfig } from "vite";

export default defineConfig({
  publicDir: "local_only/public",
  base: "./",
  build: { chunkSizeWarningLimit: 650 },
});
