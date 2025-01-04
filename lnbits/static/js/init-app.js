// Utility function to dynamically load a script
function loadScript(src) {
  return new Promise((resolve, reject) => {
    if (document.querySelector(`script[src="${src}"]`)) {
      resolve(); // Already loaded
      return;
    }

    const script = document.createElement('script');
    script.src = src;
    script.async = true;
    script.onload = resolve;
    script.onerror = () => reject(new Error(`Failed to load script: ${src}`));
    document.head.appendChild(script);
  });
}

// Function to load content based on the route
function loadContent(route) {
  const contentContainer = document.getElementById('content-container');
  const routes = {
    '/wallet': '/wallet',
   // '/admin': '/admin',
    // Add other routes here
  };

  if (routes[route]) {
    // Load the scripts first
    loadScript('/static/js/base.js')
      .then(() => loadScript('/static/js/components.js'))
      .then(() => loadScript('/static/js/wallet.js'))
      .then(() => {
        // Now fetch the content after all scripts are loaded
        return fetch(routes[route], {
          credentials: 'include',
          headers: {
            'Accept': 'text/html',
            'X-Requested-With': 'XMLHttpRequest',
          },
        });
      })
      .then((response) => response.text())
      .then((html) => {
        contentContainer.innerHTML = html; // Inject the HTML content
        // After the content is injected, initialize Vue if necessary

      })
      .catch((error) => {
        console.error('Error loading content or scripts:', error);
      });
  } else {
    console.error('Route not found');
  }
}

// Event listener to detect route change
window.addEventListener('popstate', function () {
  const currentRoute = window.location.pathname;
  loadContent(currentRoute); // Load content for the current route
});

// Function to navigate to a new route and load content
function navigateTo(route) {
  window.history.pushState({}, '', route);
  loadContent(route);
}

// Initial content load based on the current path
loadContent(window.location.pathname);

// Listen for route changes and load corresponding content
function onRouteChange() {
  const currentRoute = window.location.pathname;
  loadContent(currentRoute);
}

window.addEventListener('popstate', onRouteChange);

console.log('Scripts loaded and content injected.');
window.app.use(VueQrcodeReader);
window.app.use(Quasar);
window.app.use(window.i18n);
window.app.mount('#vue');
