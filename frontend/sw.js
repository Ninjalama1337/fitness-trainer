const CACHE = "fitness-trainer-v13";
const ASSETS = [
  "/",
  "/index.html",
  "/css/style.css?v=13",
  "/js/app.js?v=13",
  "/manifest.json",
  "/icons/icon.svg",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.pathname.startsWith("/api/")) return;

  const isHTML = event.request.mode === "navigate" || url.pathname === "/";

  if (isHTML) {
    // HTML immer aktuell vom Netz, Cache nur als Offline-Fallback
    event.respondWith(
      fetch(event.request)
        .then((res) => {
          const clone = res.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, clone));
          return res;
        })
        .catch(() => caches.match(event.request).then((c) => c || caches.match("/index.html")))
    );
    return;
  }

  // Statische Assets: cache-first mit Fallback aufs Netz
  event.respondWith(
    caches.match(event.request).then((cached) => {
      if (cached) return cached;
      return fetch(event.request)
        .then((res) => {
          if (res.ok && url.origin === self.location.origin) {
            const clone = res.clone();
            caches.open(CACHE).then((cache) => cache.put(event.request, clone));
          }
          return res;
        })
        .catch(() => caches.match("/index.html"));
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  let url = "/";
  if (event.notification.tag === "sync-error") url = "/#settings";
  if (event.notification.tag === "plan-today") url = "/#plan";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ("navigate" in client) {
          client.navigate(url);
          return client.focus();
        }
      }
      return self.clients.openWindow(url);
    })
  );
});

self.addEventListener("push", (event) => {
  let data = { title: "Fitness Trainer", body: "" };
  try {
    if (event.data) data = event.data.json();
  } catch (e) {}
  event.waitUntil(
    self.registration.showNotification(data.title || "Fitness Trainer", {
      body: data.body || "",
      tag: data.tag || "fitness",
      icon: "/icons/icon.svg",
      badge: "/icons/icon.svg",
    })
  );
});
