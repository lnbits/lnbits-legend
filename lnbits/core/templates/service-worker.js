// update cache version every time there is a new deployment
// so the service worker reinitializes the cache
const CURRENT_CACHE = 'lnbits-{{ cache_version }}-'

const getApiKey = request => {
  let api_key = request.headers.get('X-Api-Key')
  if (!api_key || api_key == 'undefined') {
    api_key = 'no_api_key'
  }
  return api_key
}

// on activation we clean up the previously registered service workers
self.addEventListener('activate', evt =>
  evt.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cacheName => {
          if (!cacheName.startsWith(CURRENT_CACHE)) {
            return caches.delete(cacheName)
          }
        })
      )
    })
  )
)

// The fetch handler serves responses for same-origin resources from a cache.
// If no response is found, it populates the runtime cache with the response
// from the network before returning it to the page.
self.addEventListener('fetch', event => {
  if (
    !event.request.url.startsWith(
      self.location.origin + '/api/v1/payments/sse'
    ) &&
    event.request.url.startsWith(self.location.origin) &&
    event.request.method == 'GET'
  ) {
    // Open the cache
    event.respondWith(
      caches.open(CURRENT_CACHE + getApiKey(event.request)).then(cache => {
        // Go to the network first
        return fetch(event.request)
          .then(fetchedResponse => {
            cache.put(event.request, fetchedResponse.clone())

            return fetchedResponse
          })
          .catch(() => {
            // If the network is unavailable, get
            return cache.match(event.request.url)
          })
      })
    )
  }
})

// Handle and show incoming push notifications
self.addEventListener('push', function (event) {
  if (!(self.Notification && self.Notification.permission === 'granted')) {
    return
  }

  let data = event.data.json()
  const title = data.title
  const body = data.body
  const url = data.url

  event.waitUntil(
    self.registration.showNotification(title, {
      body: body,
      icon: '/lnbits/favicon.ico',
      data: {
        url: url
      }
    })
  )
})

// User can click on the notification message to open wallet
// Installed app will open when `url_handlers` in web app manifest is supported
self.addEventListener('notificationclick', function (event) {
  event.notification.close()
  event.waitUntil(clients.openWindow(event.notification.data.url))
})
