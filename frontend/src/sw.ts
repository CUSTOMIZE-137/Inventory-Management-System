/// <reference lib="webworker" />
import { BackgroundSyncPlugin } from "workbox-background-sync";
import { clientsClaim } from "workbox-core";
import { ExpirationPlugin } from "workbox-expiration";
import { cleanupOutdatedCaches, createHandlerBoundToURL, precacheAndRoute } from "workbox-precaching";
import { NavigationRoute, registerRoute } from "workbox-routing";
import { CacheFirst, NetworkFirst, NetworkOnly } from "workbox-strategies";

declare const self: ServiceWorkerGlobalScope;

// Immediately activate updated service workers and claim open tabs so UI updates apply at once.
self.skipWaiting();
clientsClaim();

// 1. Precache & serve the Vite-built App Shell (CacheFirst via Workbox precache).
cleanupOutdatedCaches();
precacheAndRoute(self.__WB_MANIFEST);

// SPA navigation fallback (excluding API and OpenAPI docs).
registerRoute(
  new NavigationRoute(createHandlerBoundToURL("/index.html"), {
    denylist: [/^\/api\//, /^\/docs/, /^\/redoc/, /^\/openapi\.json/],
  })
);

// 2. Runtime CacheFirst for static App Shell assets (styles, scripts, images, fonts).
registerRoute(
  ({ request }) =>
    request.destination === "style" ||
    request.destination === "script" ||
    request.destination === "font" ||
    request.destination === "image",
  new CacheFirst({
    cacheName: "app-shell-assets",
    plugins: [
      new ExpirationPlugin({
        maxEntries: 120,
        maxAgeSeconds: 30 * 24 * 60 * 60,
      }),
    ],
  })
);

// 3. NetworkFirst for live catalog queries so stock balances stay fresh online and available offline.
registerRoute(
  ({ url, request }) => request.method === "GET" && url.pathname.startsWith("/api/v1/items"),
  new NetworkFirst({
    cacheName: "items-catalog-cache",
    networkTimeoutSeconds: 4,
    plugins: [
      new ExpirationPlugin({
        maxEntries: 60,
        maxAgeSeconds: 24 * 60 * 60,
      }),
    ],
  })
);

// 4. Workbox BackgroundSyncPlugin for offline STOCK_IN / STOCK_OUT / ADJUSTMENT replay.
const stockTransactionSyncPlugin = new BackgroundSyncPlugin("stock-transactions-queue", {
  maxRetentionTime: 24 * 60,
});

registerRoute(
  ({ url, request }) =>
    request.method === "POST" && url.pathname.startsWith("/api/v1/transactions"),
  new NetworkOnly({
    plugins: [stockTransactionSyncPlugin],
  }),
  "POST"
);

self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") {
    void self.skipWaiting();
  }
});
