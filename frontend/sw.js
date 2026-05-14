const CACHE = 'mcallen-v2';
const IMMUTABLE = [
  '/manifest.json',
  '/icon.svg',
  '/icon-maskable.svg',
  'https://cdn.jsdelivr.net/npm/marked/marked.min.js',
  'https://s3.tradingview.com/tv.js',
];

// Install — pre-cache only truly static assets (not index.html)
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => c.addAll(IMMUTABLE.filter(u => u.startsWith('/'))))
      .then(() => self.skipWaiting())
  );
});

// Activate — purge old caches immediately
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // API & SSE — always network, never cache
  if (['/analyze/', '/history', '/price/', '/explain', '/health'].some(p => url.pathname.startsWith(p))) {
    e.respondWith(fetch(e.request));
    return;
  }

  // HTML navigation — network-first so deployments are picked up immediately
  if (e.request.mode === 'navigate') {
    e.respondWith(
      fetch(e.request).catch(() => caches.match('/'))
    );
    return;
  }

  // Icons, manifest, CDN libs — cache-first (these never change)
  e.respondWith(
    caches.match(e.request).then(cached => {
      if (cached) return cached;
      return fetch(e.request).then(res => {
        if (res.ok && e.request.method === 'GET') {
          caches.open(CACHE).then(c => c.put(e.request, res.clone()));
        }
        return res;
      });
    })
  );
});
