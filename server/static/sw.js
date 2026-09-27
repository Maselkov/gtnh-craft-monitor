// Service worker, registered by crafts.js and served at /sw.js (see
// gcm/routes/pages.py). It exists only for notifications: it shows the
// server's Web Push messages (gcm/push.py) even with no tab open, and
// the page's own ones too, since Android Chrome refuses `new
// Notification()` from a page. Nothing is cached here - every request
// still goes straight to the network.

const ICON = '/icons?path=item%2Fappliedenergistics2%2Ftile.BlockInterface~0.png';

self.addEventListener('install', () => self.skipWaiting());

// Every push must show a notification - browsers revoke the
// subscription of a worker that receives pushes silently.
self.addEventListener('push', (event) => {
  let message = {};
  try { message = event.data ? event.data.json() : {}; } catch (e) { /* show the fallback */ }
  event.waitUntil(self.registration.showNotification(message.title || 'GTNH Monitor', {
    body: message.body || 'A pinned craft ended.',
    tag: message.tag,
    icon: ICON,
  }));
});

// Tapping a notification brings the page back (or opens it if it was closed).
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const w of windows) {
      if ('focus' in w) return w.focus();
    }
    return self.clients.openWindow('/crafts');
  })());
});
