import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const apiTarget = env.VITE_API_TARGET || "http://127.0.0.1:8000";

  const apiProxy = {
    "/api": { target: apiTarget, changeOrigin: true },
    "/health": { target: apiTarget, changeOrigin: true },
    "/ready": { target: apiTarget, changeOrigin: true },
    "/logo.png": { target: apiTarget, changeOrigin: true },
  };

  return {
    plugins: [
      react(),
      tailwindcss(),
      VitePWA({
        strategies: "injectManifest",
        srcDir: "src",
        filename: "sw.ts",
        registerType: "autoUpdate",
        injectRegister: "auto",
        includeAssets: [
          "favicon.png",
          "brand-logo.png",
          "logo.png",
          "apple-touch-icon.png",
          "og-image.png",
          "icon-192.png",
          "icon-512.png",
        ],
        manifest: {
          id: "/",
          name: "Stock Control — Inventory Management System",
          short_name: "Stock Control",
          description:
            "Offline-first, role-based stock management PWA with real-time atomic balance calculation and barcode scanning.",
          start_url: "/",
          scope: "/",
          display: "standalone",
          orientation: "any",
          theme_color: "#09090b",
          background_color: "#09090b",
          categories: ["business", "productivity"],
          icons: [
            {
              src: "/icon-192.png",
              sizes: "192x192",
              type: "image/png",
              purpose: "any maskable",
            },
            {
              src: "/icon-512.png",
              sizes: "512x512",
              type: "image/png",
              purpose: "any maskable",
            },
            {
              src: "/apple-touch-icon.png",
              sizes: "180x180",
              type: "image/png",
              purpose: "any",
            },
          ],
        },
        injectManifest: {
          globPatterns: ["**/*.{js,css,html,svg,png,woff2,wasm}"],
        },
      }),
    ],
    build: {
      sourcemap: false,
    },
    server: { port: 5173, proxy: apiProxy },
    preview: { port: 4173, proxy: apiProxy },
  };
});
