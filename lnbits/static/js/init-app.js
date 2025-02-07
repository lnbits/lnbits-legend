const DynamicComponent = {
  props: {
    fetchUrl: {
      type: String,
      required: true
    },
    scripts: {
      type: Array,
      default: () => []
    }
  },
  data() {
    return {
      keys: []
    }
  },
  async mounted() {
    await this.loadDynamicContent()
  },
  methods: {
    async loadScript(src) {
      return new Promise((resolve, reject) => {
        const existingScript = document.querySelector(`script[src="${src}"]`)
        if (existingScript) {
          existingScript.remove()
        }
        const script = document.createElement('script')
        script.src = src
        script.async = true
        script.onload = resolve
        script.onerror = () =>
          reject(new Error(`Failed to load script: ${src}`))
        document.head.appendChild(script)
      })
    },
    async loadDynamicContent() {
      this.$q.loading.show()
      try {
        const cleanUrl = this.fetchUrl.split('#')[0]
        //grab page content, need to be before loading scripts
        const response = await fetch(cleanUrl, {
          credentials: 'include',
          headers: {
            Accept: 'text/html',
            'X-Requested-With': 'XMLHttpRequest'
          }
        })

        const html = await response.text()

        // load window variables
        const parser = new DOMParser()
        const htmlDocument = parser.parseFromString(html, 'text/html')
        const inlineScript = htmlDocument.querySelector('#window-vars-script')
        if (inlineScript) {
          new Function(inlineScript.innerHTML)() // Execute the script
        }

        //load scripts defined in the route
        await this.loadScript('/static/js/base.js')
        for (const script of this.scripts) {
          await this.loadScript(script)
        }

        //housecleaning, remove old component
        const previousRouteName =
          this.$router.currentRoute.value.meta.previousRouteName
        if (
          previousRouteName &&
          window.app._context.components[previousRouteName]
        ) {
          delete window.app._context.components[previousRouteName]
        }

        //load component logic
        const logicKey = `${this.$route.name}PageLogic`
        const componentLogic = window[logicKey]

        if (!componentLogic) {
          throw new Error(
            `Component logic '${logicKey}' not found. Ensure it is defined in the script.`
          )
        }

        //Add mixins
        componentLogic.mixins = componentLogic.mixins || []
        if (window.windowMixin) {
          componentLogic.mixins.push(window.windowMixin)
        }

        //Build component
        window.app.component(this.$route.name, {
          ...componentLogic,
          template: html // Use the fetched HTML as the template
        })
        delete window[logicKey] //dont need this anymore
        this.$forceUpdate()
      } catch (error) {
        console.error('Error loading dynamic content:', error)
      } finally {
        this.$q.loading.hide()
      }
    }
  },
  watch: {
    $route(to, from) {
      const validRouteNames = routes.map(route => route.name)
      if (validRouteNames.includes(to.name)) {
        this.$router.currentRoute.value.meta.previousRouteName = from.name
        this.loadDynamicContent()
      } else {
        console.log(
          `Route '${to.name}' is not valid. Leave this one to Fastapi.`
        )
      }
    }
  },
  template: `
      <component :is="$route.name"></component>
  `
}

const routes = [
  {
    path: '/wallet',
    name: 'Wallet',
    component: DynamicComponent,
    props: route => {
      let fetchUrl = '/wallet'
      if (Object.keys(route.query).length > 0) {
        fetchUrl += '?'
        for (const [key, value] of Object.entries(route.query)) {
          fetchUrl += `${key}=${value}&`
        }
        fetchUrl = fetchUrl.slice(0, -1) // remove last &
      }
      return {
        fetchUrl,
        scripts: ['/static/js/wallet.js']
      }
    }
  },
  {
    path: '/admin',
    name: 'Admin',
    component: DynamicComponent,
    props: {
      fetchUrl: '/admin',
      scripts: ['/static/js/admin.js']
    }
  },
  {
    path: '/users',
    name: 'Users',
    component: DynamicComponent,
    props: {
      fetchUrl: '/users',
      scripts: ['/static/js/users.js']
    }
  },
  {
    path: '/audit',
    name: 'Audit',
    component: DynamicComponent,
    props: {
      fetchUrl: '/audit',
      scripts: ['/static/js/audit.js']
    }
  },
  {
    path: '/payments',
    name: 'Payments',
    component: DynamicComponent,
    props: {
      fetchUrl: '/payments',
      scripts: ['/static/js/payments.js']
    }
  },
  {
    path: '/extensions',
    name: 'Extensions',
    component: DynamicComponent,
    props: {
      fetchUrl: '/extensions',
      scripts: ['/static/js/extensions.js']
    }
  },
  {
    path: '/account',
    name: 'Account',
    component: DynamicComponent,
    props: {
      fetchUrl: '/account',
      scripts: ['/static/js/account.js']
    }
  },
  {
    path: '/node',
    name: 'Node',
    component: DynamicComponent,
    props: {
      fetchUrl: '/node',
      scripts: ['/static/js/node.js']
    }
  }
]

window.router = VueRouter.createRouter({
  history: VueRouter.createWebHistory(),
  routes
})

window.app.mixin({
  computed: {
    isVueRoute() {
      const currentPath = window.location.pathname
      const matchedRoute = window.router.resolve(currentPath)
      const isVueRoute = matchedRoute?.matched?.length > 0
      return isVueRoute
    }
  }
})

window.app.use(VueQrcodeReader)
window.app.use(Quasar, {
  config: {
    loading: {
      spinner: Quasar.QSpinnerBars
    }
  }
})
window.app.use(window.i18n)
window.app.provide('g', g)
window.app.use(window.router)
window.app.component('DynamicComponent', DynamicComponent)
window.app.mount('#vue')
