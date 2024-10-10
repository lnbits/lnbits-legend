window.LOCALE = 'en'
window.i18n = new VueI18n.createI18n({
  locale: window.LOCALE,
  fallbackLocale: window.LOCALE,
  messages: window.localisation
})

window.LNbits = {
  api: {
    request: function (method, url, apiKey, data) {
      return axios({
        method: method,
        url: url,
        headers: {
          'X-Api-Key': apiKey
        },
        data: data
      })
    },
    getServerHealth: function () {
      return this.request('get', '/api/v1/health')
    },
    createInvoice: async function (
      wallet,
      amount,
      memo,
      unit = 'sat',
      lnurlCallback = null
    ) {
      return this.request('post', '/api/v1/payments', wallet.inkey, {
        out: false,
        amount: amount,
        memo: memo,
        lnurl_callback: lnurlCallback,
        unit: unit
      })
    },
    payInvoice: function (wallet, bolt11) {
      return this.request('post', '/api/v1/payments', wallet.adminkey, {
        out: true,
        bolt11: bolt11
      })
    },
    payLnurl: function (
      wallet,
      callback,
      description_hash,
      amount,
      description = '',
      comment = '',
      unit = ''
    ) {
      return this.request('post', '/api/v1/payments/lnurl', wallet.adminkey, {
        callback,
        description_hash,
        amount,
        comment,
        description,
        unit
      })
    },
    authLnurl: function (wallet, callback) {
      return this.request('post', '/api/v1/lnurlauth', wallet.adminkey, {
        callback
      })
    },
    createAccount: function (name) {
      return this.request('post', '/api/v1/account', null, {
        name: name
      })
    },
    register: function (username, email, password, password_repeat) {
      return axios({
        method: 'POST',
        url: '/api/v1/auth/register',
        data: {
          username,
          email,
          password,
          password_repeat
        }
      })
    },
    reset: function (reset_key, password, password_repeat) {
      return axios({
        method: 'PUT',
        url: '/api/v1/auth/reset',
        data: {
          reset_key,
          password,
          password_repeat
        }
      })
    },
    login: function (username, password) {
      return axios({
        method: 'POST',
        url: '/api/v1/auth',
        data: {username, password}
      })
    },
    loginByProvider: function (provider, headers, data) {
      return axios({
        method: 'POST',
        url: `/api/v1/auth/${provider}`,
        headers: headers,
        data
      })
    },
    loginUsr: function (usr) {
      return axios({
        method: 'POST',
        url: '/api/v1/auth/usr',
        data: {usr}
      })
    },
    logout: function () {
      return axios({
        method: 'POST',
        url: '/api/v1/auth/logout'
      })
    },
    getAuthenticatedUser: function () {
      return this.request('get', '/api/v1/auth')
    },
    getWallet: function (wallet) {
      return this.request('get', '/api/v1/wallet', wallet.inkey)
    },
    createWallet: function (wallet, name) {
      return this.request('post', '/api/v1/wallet', wallet.adminkey, {
        name: name
      }).then(res => {
        window.location = '/wallet?wal=' + res.data.id
      })
    },
    updateWallet: function (name, wallet) {
      return this.request('patch', '/api/v1/wallet', wallet.adminkey, {
        name: name
      })
    },
    deleteWallet: function (wallet) {
      return this.request('delete', '/api/v1/wallet', wallet.adminkey).then(
        _ => {
          let url = new URL(window.location.href)
          url.searchParams.delete('wal')
          window.location = url
        }
      )
    },
    getPayments: function (wallet, params) {
      return this.request(
        'get',
        '/api/v1/payments/paginated?' + params,
        wallet.inkey
      )
    },
    getPayment: function (wallet, paymentHash) {
      return this.request(
        'get',
        '/api/v1/payments/' + paymentHash,
        wallet.inkey
      )
    },
    updateBalance: function (credit, wallet_id) {
      return this.request('PUT', '/users/api/v1/topup', null, {
        amount: credit,
        id: wallet_id
      })
    },
    getCurrencies() {
      return this.request('GET', '/api/v1/currencies').then(response => {
        return ['sats', ...response.data]
      })
    }
  },
  events: {
    onInvoicePaid: function (wallet, cb) {
      let listener = ev => {
        cb(JSON.parse(ev.data))
      }
      this.listenersCount = this.listenersCount || {[wallet.inkey]: 0}
      this.listenersCount[wallet.inkey]++

      this.listeners = this.listeners || {}
      if (!(wallet.inkey in this.listeners)) {
        this.listeners[wallet.inkey] = new EventSource(
          '/api/v1/payments/sse?api-key=' + wallet.inkey
        )
      }

      this.listeners[wallet.inkey].addEventListener(
        'payment-received',
        listener
      )

      return () => {
        this.listeners[wallet.inkey].removeEventListener(
          'payment-received',
          listener
        )
        this.listenersCount[wallet.inkey]--

        if (this.listenersCount[wallet.inkey] <= 0) {
          this.listeners[wallet.inkey].close()
          delete this.listeners[wallet.inkey]
        }
      }
    }
  },
  map: {
    extension: function (data) {
      const obj = _.object(
        [
          'code',
          'isValid',
          'isAdminOnly',
          'name',
          'shortDescription',
          'tile',
          'contributors',
          'hidden'
        ],
        data
      )
      obj.url = ['/', obj.code, '/'].join('')
      return obj
    },
    user: function (data) {
      const obj = {
        id: data.id,
        admin: data.admin,
        email: data.email,
        extensions: data.extensions,
        wallets: data.wallets,
        admin: data.admin
      }
      const mapWallet = this.wallet
      obj.wallets = obj.wallets
        .map(function (obj) {
          return mapWallet(obj)
        })
        .sort(function (a, b) {
          return a.name.localeCompare(b.name)
        })
      obj.walletOptions = obj.wallets.map(function (obj) {
        return {
          label: [obj.name, ' - ', obj.id].join(''),
          value: obj.id
        }
      })
      return obj
    },
    wallet: function (data) {
      newWallet = {
        id: data.id,
        name: data.name,
        adminkey: data.adminkey,
        inkey: data.inkey,
        currency: data.currency
      }
      newWallet.msat = data.balance_msat
      newWallet.sat = Math.floor(data.balance_msat / 1000)
      newWallet.fsat = new Intl.NumberFormat(window.LOCALE).format(
        newWallet.sat
      )
      newWallet.url = `/wallet?&wal=${data.id}`
      return newWallet
    },
    payment: function (data) {
      obj = {
        checking_id: data.checking_id,
        status: data.status,
        amount: data.amount,
        fee: data.fee,
        memo: data.memo,
        time: data.time,
        bolt11: data.bolt11,
        preimage: data.preimage,
        payment_hash: data.payment_hash,
        expiry: data.expiry,
        extra: data.extra ?? {},
        wallet_id: data.wallet_id,
        webhook: data.webhook,
        webhook_status: data.webhook_status,
        fiat_amount: data.fiat_amount,
        fiat_currency: data.fiat_currency
      }

      obj.date = Quasar.date.formatDate(
        new Date(obj.time * 1000),
        'YYYY-MM-DD HH:mm'
      )
      obj.dateFrom = moment(obj.date).fromNow()
      obj.expirydate = Quasar.date.formatDate(
        new Date(obj.expiry * 1000),
        'YYYY-MM-DD HH:mm'
      )
      obj.expirydateFrom = moment(obj.expirydate).fromNow()
      obj.msat = obj.amount
      obj.sat = obj.msat / 1000
      obj.tag = obj.extra?.tag
      obj.fsat = new Intl.NumberFormat(window.LOCALE).format(obj.sat)
      obj.isIn = obj.amount > 0
      obj.isOut = obj.amount < 0
      obj.isPending = obj.status === 'pending'
      obj.isPaid = obj.status === 'success'
      obj.isFailed = obj.status === 'failed'
      obj._q = [obj.memo, obj.sat].join(' ').toLowerCase()
      try {
        obj.details = JSON.parse(data.extra?.details || '{}')
      } catch {
        obj.details = {extraDetails: data.extra?.details}
      }
      return obj
    }
  },
  utils: {
    confirmDialog: function (msg) {
      return Quasar.Dialog.create({
        message: msg,
        ok: {
          flat: true,
          color: 'orange'
        },
        cancel: {
          flat: true,
          color: 'grey'
        }
      })
    },
    digestMessage: async function (message) {
      const msgUint8 = new TextEncoder().encode(message)
      const hashBuffer = await crypto.subtle.digest('SHA-256', msgUint8)
      const hashArray = Array.from(new Uint8Array(hashBuffer))
      const hashHex = hashArray
        .map(b => b.toString(16).padStart(2, '0'))
        .join('')
      return hashHex
    },
    formatDate: function (timestamp) {
      return Quasar.date.formatDate(
        new Date(timestamp * 1000),
        'YYYY-MM-DD HH:mm'
      )
    },
    formatCurrency: function (value, currency) {
      return new Intl.NumberFormat(window.LOCALE, {
        style: 'currency',
        currency: currency
      }).format(value)
    },
    formatSat: function (value) {
      return new Intl.NumberFormat(window.LOCALE).format(value)
    },
    formatMsat: function (value) {
      return this.formatSat(value / 1000)
    },
    notifyApiError: function (error) {
      if (!error.response) {
        return console.error(error)
      }
      const types = {
        400: 'warning',
        401: 'warning',
        500: 'negative'
      }
      Quasar.Notify.create({
        timeout: 5000,
        type: types[error.response.status] || 'warning',
        message:
          error.response.data.message || error.response.data.detail || null,
        caption:
          [error.response.status, ' ', error.response.statusText]
            .join('')
            .toUpperCase() || null,
        icon: null
      })
    },
    search: function (data, q, field, separator) {
      try {
        const queries = q.toLowerCase().split(separator || ' ')
        return data.filter(function (obj) {
          let matches = 0
          _.each(queries, function (q) {
            if (obj[field].indexOf(q) !== -1) matches++
          })
          return matches === queries.length
        })
      } catch (err) {
        return data
      }
    },
    prepareFilterQuery(tableConfig, props) {
      if (props) {
        tableConfig.pagination = props.pagination
      }
      let pagination = tableConfig.pagination
      tableConfig.loading = true
      const query = {
        limit: pagination.rowsPerPage,
        offset: (pagination.page - 1) * pagination.rowsPerPage,
        sortby: pagination.sortBy ?? '',
        direction: pagination.descending ? 'desc' : 'asc',
        ...tableConfig.filter
      }
      if (tableConfig.search) {
        query.search = tableConfig.search
      }
      return new URLSearchParams(query)
    },
    exportCSV: function (columns, data, fileName) {
      const wrapCsvValue = function (val, formatFn) {
        let formatted = formatFn !== void 0 ? formatFn(val) : val

        formatted =
          formatted === void 0 || formatted === null ? '' : String(formatted)

        formatted = formatted.split('"').join('""')

        return `"${formatted}"`
      }

      const content = [
        columns.map(function (col) {
          return wrapCsvValue(col.label)
        })
      ]
        .concat(
          data.map(function (row) {
            return columns
              .map(function (col) {
                return wrapCsvValue(
                  typeof col.field === 'function'
                    ? col.field(row)
                    : row[col.field === void 0 ? col.name : col.field],
                  col.format
                )
              })
              .join(',')
          })
        )
        .join('\r\n')

      const status = Quasar.exportFile(
        `${fileName || 'table-export'}.csv`,
        content,
        'text/csv'
      )

      if (status !== true) {
        Quasar.Notify.create({
          message: 'Browser denied file download...',
          color: 'negative',
          icon: null
        })
      }
    },
    convertMarkdown(text) {
      const converter = new showdown.Converter()
      converter.setFlavor('github')
      converter.setOption('simpleLineBreaks', true)
      return converter.makeHtml(text)
    },
    hexToRgb: function (hex) {
      return Quasar.colors.hexToRgb(hex)
    },
    hexDarken: function (hex, percent) {
      return Quasar.colors.lighten(hex, percent)
    },
    hexAlpha: function (hex, alpha) {
      return Quasar.colors.changeAlpha(hex, alpha)
    },
    getPaletteColor: function (color) {
      return Quasar.colors.getPaletteColor(color)
    }
  }
}

window.windowMixin = {
  i18n: window.i18n,
  data: function () {
    return {
      toggleSubs: true,
      reactionChoice: 'confettiBothSides',
      gradientChoice:
        this.$q.localStorage.getItem('lnbits.gradientBg') || false,
      isUserAuthorized: false,
      g: {
        offline: !navigator.onLine,
        visibleDrawer: false,
        extensions: [],
        user: null,
        wallet: null,
        payments: [],
        allowedThemes: null,
        langs: []
      }
    }
  },

  methods: {
    changeColor: function (newValue) {
      document.body.setAttribute('data-theme', newValue)
      this.$q.localStorage.set('lnbits.theme', newValue)
    },
    applyGradient: function () {
      if (this.$q.localStorage.getItem('lnbits.gradientBg')) {
        this.setColors()
        darkBgColor = this.$q.localStorage.getItem('lnbits.darkBgColor')
        primaryColor = this.$q.localStorage.getItem('lnbits.primaryColor')
        const gradientStyle = `linear-gradient(to bottom right, ${LNbits.utils.hexDarken(String(primaryColor), -70)}, #0a0a0a)`
        document.body.style.setProperty(
          'background-image',
          gradientStyle,
          'important'
        )
        const gradientStyleCards = `background-color: ${LNbits.utils.hexAlpha(String(darkBgColor), 0.4)} !important`
        const style = document.createElement('style')
        style.innerHTML =
          `body[data-theme="${this.$q.localStorage.getItem('lnbits.theme')}"] .q-card:not(.q-dialog .q-card, .lnbits__dialog-card, .q-dialog-plugin--dark), body.body${this.$q.dark.isActive ? '--dark' : ''} .q-header, body.body${this.$q.dark.isActive ? '--dark' : ''} .q-drawer { ${gradientStyleCards} }` +
          `body[data-theme="${this.$q.localStorage.getItem('lnbits.theme')}"].body--dark{background: ${LNbits.utils.hexDarken(String(primaryColor), -88)} !important; }` +
          `[data-theme="${this.$q.localStorage.getItem('lnbits.theme')}"] .q-card--dark{background: ${String(darkBgColor)} !important;} }`
        document.head.appendChild(style)
      }
    },
    setColors: function () {
      this.$q.localStorage.set(
        'lnbits.primaryColor',
        LNbits.utils.getPaletteColor('primary')
      )
      this.$q.localStorage.set(
        'lnbits.secondaryColor',
        LNbits.utils.getPaletteColor('secondary')
      )
      this.$q.localStorage.set(
        'lnbits.darkBgColor',
        LNbits.utils.getPaletteColor('dark')
      )
    },
    copyText: function (text, message, position) {
      Quasar.copyToClipboard(text).then(function () {
        Quasar.Notify.create({
          message: message || 'Copied to clipboard!',
          position: position || 'bottom'
        })
      })
    },
    checkUsrInUrl: async function () {
      try {
        const params = new URLSearchParams(window.location.search)
        const usr = params.get('usr')
        if (!usr) {
          return
        }

        if (!this.isUserAuthorized) {
          await LNbits.api.loginUsr(usr)
        }

        params.delete('usr')
        const cleanQueryPrams = params.size ? `?${params.toString()}` : ''

        window.history.replaceState(
          {},
          document.title,
          window.location.pathname + cleanQueryPrams
        )
      } finally {
        this.isUserAuthorized = !!this.$q.cookies.get(
          'is_lnbits_user_authorized'
        )
      }
    },
    logout: async function () {
      LNbits.utils
        .confirmDialog(
          'Do you really want to logout?' +
            ' Please visit "My Account" page to check your credentials!'
        )
        .onOk(async () => {
          try {
            await LNbits.api.logout()
            window.location = '/'
          } catch (e) {
            LNbits.utils.notifyApiError(e)
          }
        })
    },
    themeParams() {
      const url = new URL(window.location.href)
      const params = new URLSearchParams(window.location.search)
      const fields = ['theme', 'dark', 'gradient']
      const toBoolean = value =>
        value.trim().toLowerCase() === 'true' || value === '1'

      // Check if any of the relevant parameters ('theme', 'dark', 'gradient') are present in the URL.
      if (fields.some(param => params.has(param))) {
        const theme = params.get('theme')
        const darkMode = params.get('dark')
        const gradient = params.get('gradient')

        if (
          theme &&
          this.g.allowedThemes.includes(theme.trim().toLowerCase())
        ) {
          const normalizedTheme = theme.trim().toLowerCase()
          document.body.setAttribute('data-theme', normalizedTheme)
          this.$q.localStorage.set('lnbits.theme', normalizedTheme)
        }

        if (darkMode) {
          const isDark = toBoolean(darkMode)
          this.$q.localStorage.set('lnbits.darkMode', isDark)
          if (!isDark) {
            this.$q.localStorage.set('lnbits.gradientBg', false)
          }
        }

        if (gradient) {
          const isGradient = toBoolean(gradient)
          this.$q.localStorage.set('lnbits.gradientBg', isGradient)
          if (isGradient) {
            this.$q.localStorage.set('lnbits.darkMode', true)
          }
        }

        // Remove processed parameters
        fields.forEach(param => params.delete(param))

        window.history.replaceState(null, null, url.pathname)
      }

      this.setColors()
    }
  },
  created: async function () {
    if (
      this.$q.localStorage.getItem('lnbits.darkMode') == true ||
      this.$q.localStorage.getItem('lnbits.darkMode') == false
    ) {
      this.$q.dark.set(this.$q.localStorage.getItem('lnbits.darkMode'))
    } else {
      this.$q.dark.set(true)
    }
    this.reactionChoice =
      this.$q.localStorage.getItem('lnbits.reactions') || 'confettiBothSides'

    this.g.allowedThemes = window.allowedThemes ?? ['bitcoin']

    let locale = this.$q.localStorage.getItem('lnbits.lang')
    if (locale) {
      window.LOCALE = locale
      window.i18n.global.locale = locale
    }

    this.g.langs = window.langs ?? []

    addEventListener('offline', event => {
      this.g.offline = true
    })

    addEventListener('online', event => {
      this.g.offline = false
    })

    // failsafe if admin changes themes halfway
    if (!this.$q.localStorage.getItem('lnbits.theme')) {
      this.changeColor(this.g.allowedThemes[0])
    }
    if (
      this.$q.localStorage.getItem('lnbits.theme') &&
      !this.g.allowedThemes.includes(
        this.$q.localStorage.getItem('lnbits.theme')
      )
    ) {
      this.changeColor(this.g.allowedThemes[0])
    }

    if (this.$q.localStorage.getItem('lnbits.theme')) {
      document.body.setAttribute(
        'data-theme',
        this.$q.localStorage.getItem('lnbits.theme')
      )
    }

    this.applyGradient()

    if (window.user) {
      this.g.user = Object.freeze(window.LNbits.map.user(window.user))
    }
    if (window.wallet) {
      this.g.wallet = Object.freeze(window.LNbits.map.wallet(window.wallet))
    }
    if (window.extensions) {
      const user = this.g.user
      const extensions = Object.freeze(
        window.extensions
          .map(function (data) {
            return window.LNbits.map.extension(data)
          })
          .filter(function (obj) {
            return !obj.hidden
          })
          .filter(function (obj) {
            if (window.user?.admin) return obj
            return !obj.isAdminOnly
          })
          .map(function (obj) {
            if (user) {
              obj.isEnabled = user.extensions.indexOf(obj.code) !== -1
            } else {
              obj.isEnabled = false
            }
            return obj
          })
          .sort(function (a, b) {
            const nameA = a.name.toUpperCase()
            const nameB = b.name.toUpperCase()
            return nameA < nameB ? -1 : nameA > nameB ? 1 : 0
          })
      )

      this.g.extensions = extensions
    }
    await this.checkUsrInUrl()
    this.themeParams()
  }
}

window.decryptLnurlPayAES = function (success_action, preimage) {
  let keyb = new Uint8Array(
    preimage.match(/[\da-f]{2}/gi).map(h => parseInt(h, 16))
  )

  return crypto.subtle
    .importKey('raw', keyb, {name: 'AES-CBC', length: 256}, false, ['decrypt'])
    .then(key => {
      let ivb = Uint8Array.from(window.atob(success_action.iv), c =>
        c.charCodeAt(0)
      )
      let ciphertextb = Uint8Array.from(
        window.atob(success_action.ciphertext),
        c => c.charCodeAt(0)
      )

      return crypto.subtle.decrypt({name: 'AES-CBC', iv: ivb}, key, ciphertextb)
    })
    .then(valueb => {
      let decoder = new TextDecoder('utf-8')
      return decoder.decode(valueb)
    })
}
