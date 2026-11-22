/* نيكست موبايل - service worker: يخزن ملفات التصميم فقط، البيانات دائمًا من الكمبيوتر */
const CACHE = "nixt-mobile-v1";
const ASSETS = [
  "/mobile",
  "/manifest.json",
  "/static/logo.png",
  "/static/favicon.png",
  "/static/icon-192.png",
  "/static/icon-512.png",
  "/static/fonts/cairo-arabic.woff2",
  "/static/fonts/cairo-latin.woff2"
];
self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(
    ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.startsWith("/api/") || url.pathname === "/m") {
    return; // البيانات دائمًا live من الكمبيوتر
  }
  e.respondWith(
    caches.match(e.request).then(hit => hit || fetch(e.request).then(r => {
      const copy = r.clone();
      caches.open(CACHE).then(c => c.put(e.request, copy));
      return r;
    }))
  );
});
