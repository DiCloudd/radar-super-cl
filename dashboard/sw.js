// Red primero (los datos cambian cada ventana); caché solo como respaldo sin conexión.
const CACHE = "radar-v3";
self.addEventListener("install", e => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", e => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.origin !== location.origin) return;
  e.respondWith(fetch(e.request).then(r => {
    if (r.ok && !u.pathname.endsWith("products.json")) {
      const copy = r.clone();
      caches.open(CACHE).then(c => c.put(u.pathname, copy));
    }
    return r;
  }).catch(() => caches.match(u.pathname)));
});
