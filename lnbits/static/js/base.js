window.LNbits = {
  g: window.g,
  api: {
    request(method, url, apiKey, data) {
      return axios({
        method: method,
        url: url,
        headers: {
          'X-Api-Key': apiKey
        },
        data: data
      })
    },
    getServerHealth() {
      return this.request('get', '/api/v1/health')
    },
    async createInvoice(
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
    payInvoice(wallet, bolt11) {
      return this.request('post', '/api/v1/payments', wallet.adminkey, {
        out: true,
        bolt11: bolt11
      })
    },
    payLnurl(
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
    authLnurl(wallet, callback) {
      return this.request('post', '/api/v1/lnurlauth', wallet.adminkey, {
        callback
      })
    },
    createAccount(name) {
      return this.request('post', '/api/v1/account', null, {
        name: name
      })
    },
    register(username, email, password, password_repeat) {
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
    reset(reset_key, password, password_repeat) {
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
    login(username, password) {
      return axios({
        method: 'POST',
        url: '/api/v1/auth',
        data: {username, password}
      })
    },
    loginByProvider(provider, headers, data) {
      return axios({
        method: 'POST',
        url: `/api/v1/auth/${provider}`,
        headers: headers,
        data
      })
    },
    loginUsr(usr) {
      return axios({
        method: 'POST',
        url: '/api/v1/auth/usr',
        data: {usr}
      })
    },
    logout() {
      return axios({
        method: 'POST',
        url: '/api/v1/auth/logout'
      })
    },
    getAuthenticatedUser() {
      return this.request('get', '/api/v1/auth')
    },
    getWallet(wallet) {
      return this.request('get', '/api/v1/wallet', wallet.inkey)
    },
    createWallet(wallet, name) {
      return this.request('post', '/api/v1/wallet', wallet.adminkey, {
        name: name
      }).then(res => {
        window.location = '/wallet?wal=' + res.data.id
      })
    },
    updateWallet(name, wallet) {
      return this.request('patch', '/api/v1/wallet', wallet.adminkey, {
        name: name
      })
    },
    resetWalletKeys(wallet) {
      return this.request('put', `/api/v1/wallet/reset/${wallet.id}`).then(
        res => {
          return res.data
        }
      )
    },
    deleteWallet(wallet) {
      return this.request('delete', `/api/v1/wallet/${wallet.id}`).then(_ => {
        let url = new URL(window.location.href)
        url.searchParams.delete('wal')
        window.location = url
      })
    },
    getPayments(wallet, params) {
      return this.request(
        'get',
        '/api/v1/payments/paginated?' + params,
        wallet.inkey
      )
    },
    getPayment(wallet, paymentHash) {
      return this.request(
        'get',
        '/api/v1/payments/' + paymentHash,
        wallet.inkey
      )
    },
    updateBalance(credit, wallet_id) {
      return this.request('PUT', '/users/api/v1/balance', null, {
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
    onInvoicePaid(wallet, cb) {
      ws = new WebSocket(`${websocketUrl}/${wallet.inkey}`)
      ws.onmessage = ev => {
        const data = JSON.parse(ev.data)
        if (data.payment) {
          cb(data)
        }
      }
      return ws.onclose
    }
  },
  map: {
    extension(data) {
      const obj = {...data}
      obj.url = ['/', obj.code, '/'].join('')
      return obj
    },
    user(data) {
      const obj = {
        id: data.id,
        admin: data.admin,
        email: data.email,
        extensions: data.extensions,
        wallets: data.wallets,
        super_user: data.super_user
      }
      const mapWallet = this.wallet
      obj.wallets = obj.wallets
        .map(obj => {
          return mapWallet(obj)
        })
        .sort((a, b) => {
          return a.name.localeCompare(b.name)
        })
      obj.walletOptions = obj.wallets.map(obj => {
        return {
          label: [obj.name, ' - ', obj.id].join(''),
          value: obj.id
        }
      })
      return obj
    },
    wallet(data) {
      newWallet = {
        id: data.id,
        name: data.name,
        adminkey: data.adminkey,
        inkey: data.inkey,
        currency: data.currency,
        extra: data.extra
      }
      newWallet.msat = data.balance_msat
      newWallet.sat = Math.floor(data.balance_msat / 1000)
      newWallet.fsat = new Intl.NumberFormat(window.LOCALE).format(
        newWallet.sat
      )
      newWallet.url = `/wallet?&wal=${data.id}`
      return newWallet
    },
    payment(data) {
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

      obj.date = Quasar.date.formatDate(new Date(obj.time), window.dateFormat)
      obj.dateFrom = moment(obj.date).fromNow()
      obj.expirydate = Quasar.date.formatDate(
        new Date(obj.expiry),
        window.dateFormat
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
    confirmDialog(msg) {
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
    async digestMessage(message) {
      const msgUint8 = new TextEncoder().encode(message)
      const hashBuffer = await crypto.subtle.digest('SHA-256', msgUint8)
      const hashArray = Array.from(new Uint8Array(hashBuffer))
      const hashHex = hashArray
        .map(b => b.toString(16).padStart(2, '0'))
        .join('')
      return hashHex
    },
    formatDate(timestamp) {
      return Quasar.date.formatDate(
        new Date(timestamp * 1000),
        window.dateFormat
      )
    },
    formatDateString(isoDateString) {
      return Quasar.date.formatDate(new Date(isoDateString), window.dateFormat)
    },
    formatCurrency(value, currency) {
      return new Intl.NumberFormat(window.LOCALE, {
        style: 'currency',
        currency: currency
      }).format(value)
    },
    formatSat(value) {
      return new Intl.NumberFormat(window.LOCALE).format(value)
    },
    formatMsat(value) {
      return this.formatSat(value / 1000)
    },
    notifyApiError(error) {
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
    search(data, q, field, separator) {
      try {
        const queries = q.toLowerCase().split(separator || ' ')
        return data.filter(obj => {
          let matches = 0
          _.each(queries, q => {
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
        tableConfig.filter = props.filter
      }
      const pagination = tableConfig.pagination
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
    exportCSV(columns, data, fileName) {
      const wrapCsvValue = (val, formatFn) => {
        let formatted = formatFn !== void 0 ? formatFn(val) : val

        formatted =
          formatted === void 0 || formatted === null ? '' : String(formatted)

        formatted = formatted.split('"').join('""')

        return `"${formatted}"`
      }

      const content = [
        columns.map(col => {
          return wrapCsvValue(col.label)
        })
      ]
        .concat(
          data.map(row => {
            return columns
              .map(col => {
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
    hexToRgb(hex) {
      return Quasar.colors.hexToRgb(hex)
    },
    hexDarken(hex, percent) {
      return Quasar.colors.lighten(hex, percent)
    },
    hexAlpha(hex, alpha) {
      return Quasar.colors.changeAlpha(hex, alpha)
    },
    getPaletteColor(color) {
      return Quasar.colors.getPaletteColor(color)
    }
  }
}

if (!window.g) {
  window.g = Vue.reactive({
    offline: !navigator.onLine,
    visibleDrawer: false,
    extensions: [],
    user: null,
    wallet: {},
    fiatBalance: 0,
    exchangeRate: 0,
    fiatTracking: false,
    wallets: [],
    payments: [],
    allowedThemes: null,
    langs: [],
    walletEventListeners: [],
    updatePayments: false,
    updatePaymentsHash: ''
  })
}

window.windowMixin = {
  i18n: window.i18n,
  data() {
    return {
      g: window.g,
      toggleSubs: true,
      mobileSimple: true,
      walletFlip: true,
      borderSelection: null,
      gradientSelection: null,
      themeSelection: null,
      reactionSelection: null,
      bgimageSelection: null,
      gradientSelection: false,
      borderChoice: this.$q.localStorage.has('lnbits.border')
        ? this.$q.localStorage.getItem('lnbits.border')
        : USE_DEFAULT_BORDER,
      gradientChoice: this.$q.localStorage.has('lnbits.gradientBg')
        ? this.$q.localStorage.getItem('lnbits.gradientBg')
        : USE_DEFAULT_GRADIENT,
      themeChoice: this.$q.localStorage.has('lnbits.theme')
        ? this.$q.localStorage.getItem('lnbits.theme')
        : USE_DEFAULT_THEME,
      reactionChoice: this.$q.localStorage.has('lnbits.reactions')
        ? this.$q.localStorage.getItem('lnbits.reactions')
        : USE_DEFAULT_REACTION,
      bgimageChoice: this.$q.localStorage.has('lnbits.backgroundImage')
        ? this.$q.localStorage.getItem('lnbits.backgroundImage')
        : USE_DEFAULT_BGIMAGE,
      isUserAuthorized: false,
      walletEventListeners: [],
      backgroundImage: ''
    }
  },

  methods: {
    flipWallets(smallScreen) {
      this.walletFlip = !this.walletFlip
      if (this.walletFlip && smallScreen) {
        this.g.visibleDrawer = false
      }
      this.$q.localStorage.set('lnbits.walletFlip', this.walletFlip)
    },
    simpleMobile() {
      this.$q.localStorage.set('lnbits.mobileSimple', !this.mobileSimple)
      this.refreshRoute()
    },
    paymentEvents() {
      this.g.walletEventListeners = this.g.walletEventListeners || []
      this.g.user.wallets.forEach(wallet => {
        if (!this.g.walletEventListeners.includes(wallet.id)) {
          this.g.walletEventListeners.push(wallet.id)
          LNbits.events.onInvoicePaid(wallet, data => {
            const walletIndex = this.g.user.wallets.findIndex(
              w => w.id === wallet.id
            )
            if (walletIndex !== -1) {
              //needed for balance being deducted
              let satBalance = data.wallet_balance
              if (data.payment.amount < 0) {
                satBalance = data.wallet_balance += data.payment.amount / 1000
              }
              //update the wallet
              Object.assign(this.g.user.wallets[walletIndex], {
                sat: satBalance,
                msat: data.wallet_balance * 1000,
                fsat: data.wallet_balance.toLocaleString()
              })
              //update the current wallet
              if (this.g.wallet.id === data.payment.wallet_id) {
                Object.assign(this.g.wallet, this.g.user.wallets[walletIndex])

                //if on the wallet page and payment is incoming trigger the eventReaction
                if (
                  data.payment.amount > 0 &&
                  window.location.pathname === '/wallet'
                ) {
                  eventReaction(data.wallet_balance * 1000)
                }
              }
            }
            this.g.updatePaymentsHash = data.payment.payment_hash
            this.g.updatePayments = !this.g.updatePayments
          })
        }
      })
    },
    selectWallet(wallet) {
      Object.assign(this.g.wallet, wallet)
      // this.wallet = this.g.wallet
      this.g.updatePayments = !this.g.updatePayments
      this.balance = parseInt(wallet.balance_msat / 1000)
      const currentPath = this.$route.path
      if (currentPath !== '/wallet') {
        this.$router.push({
          path: '/wallet',
          query: {wal: this.g.wallet.id}
        })
      } else {
        const url = new URL(window.location.href)
        url.searchParams.set('wal', this.g.wallet.id)
        window.history.replaceState({}, '', url.toString())
      }
    },
    changeTheme(newValue) {
      document.body.setAttribute('data-theme', newValue)
      if (this.themeSelection) {
        this.$q.localStorage.set('lnbits.theme', newValue)
      }
      this.setColors()
    },
    applyGradient() {
      darkBgColor = this.$q.localStorage.getItem('lnbits.darkBgColor')
      primaryColor = this.$q.localStorage.getItem('lnbits.primaryColor')
      if (this.gradientChoice) {
        if (!this.$q.dark.isActive) {
          this.$q.dark.toggle()
          this.$q.localStorage.set('lnbits.darkMode', true)
        }
        if (this.gradientSelection) {
          this.$q.localStorage.set('lnbits.gradientBg', true)
        }
        const gradientStyle = `linear-gradient(to bottom right, ${LNbits.utils.hexDarken(String(primaryColor), -70)}, #0a0a0a)`
        document.body.style.setProperty(
          'background-image',
          gradientStyle,
          'important'
        )
        const gradientStyleCards = `background-color: ${LNbits.utils.hexAlpha(String(darkBgColor), 0.4)} !important`
        const style = document.createElement('style')
        style.innerHTML =
          `body[data-theme="${this.themeChoice}"] .q-card:not(.q-dialog .q-card, .lnbits__dialog-card, .q-dialog-plugin--dark), body.body${this.$q.dark.isActive ? '--dark' : ''} .q-header, body.body${this.$q.dark.isActive ? '--dark' : ''} .q-drawer { ${gradientStyleCards} }` +
          `body[data-theme="${this.themeChoice}"].body--dark{background: ${LNbits.utils.hexDarken(String(primaryColor), -88)} !important; }` +
          `[data-theme="${this.themeChoice}"] .q-card--dark{background: ${String(darkBgColor)} !important;} }`
        document.head.appendChild(style)
      } else {
        if (this.gradientSelection) {
          this.$q.localStorage.set('lnbits.gradientBg', false)
        }
      }
    },
    toggleDarkMode() {
      this.$q.dark.toggle()
      this.$q.localStorage.set('lnbits.darkMode', this.$q.dark.isActive)
      if (this.gradientChoice && !this.$q.dark.isActive) {
        window.location.reload()
      }
    },
    applyBackgroundImage() {
      if (this.bgimageSelection) {
        this.$q.localStorage.set(
          'lnbits.backgroundImage',
          this.bgimageSelection
        )
        this.bgimageChoice = this.bgimageSelection
      }
      if (
        this.bgimageChoice &&
        this.bgimageChoice !== 'null' &&
        this.bgimageChoice !== ''
      ) {
        if (!this.gradientChoice) {
          this.gradientChoice = true
          this.applyGradient()
        }
        const style = document.createElement('style')
        style.innerHTML = `
        body[data-theme="${this.themeChoice}"]::before {
          content: '';
          position: fixed;
          top: 0;
          left: 0;
          width: 100%;
          height: 100%;
          background: url(${this.bgimageChoice});
          background-size: cover;
          filter: blur(8px);
          z-index: -1;
          background-position: center;
        background-repeat: no-repeat;
        background-size: cover;
        }
        body[data-theme="${this.themeChoice}"] .q-page-container {
          backdrop-filter: none; /* Ensure the page content is not affected */
        }`
        document.head.appendChild(style)
      }
    },
    applyBorder() {
      if (this.borderSelection) {
        this.$q.localStorage.setItem('lnbits.border', this.borderSelection)
        this.borderChoice = this.$q.localStorage.getItem('lnbits.border')
      }
      let borderStyleCSS
      if (this.borderChoice == 'hard-border') {
        borderStyleCSS = `box-shadow: 0 0 0 1px rgba(0,0,0,.12), 0 0 0 1px #ffffff47; border: none;`
      }
      if (this.borderChoice == 'neon-border') {
        borderStyleCSS = `border: 2px solid ${this.$q.localStorage.getItem('lnbits.primaryColor')}; box-shadow: none;`
      }
      if (this.borderChoice == 'no-border') {
        borderStyleCSS = `box-shadow: none; border: none;`
      }
      if (this.borderChoice == 'retro-border') {
        borderStyleCSS = `border: none; border-color: rgba(255, 255, 255, 0.28); box-shadow: 0 1px 5px rgba(255, 255, 255, 0.2), 0 2px 2px rgba(255, 255, 255, 0.14), 0 3px 1px -2px rgba(255, 255, 255, 0.12);`
      }
      let style = document.createElement('style')
      style.innerHTML = `
        body[data-theme="${this.themeChoice}"] .q-card,
        body[data-theme="${this.themeChoice}"] .q-card.q-card--dark,
        body[data-theme="${this.themeChoice}"] .q-date,
        body[data-theme="${this.themeChoice}"] .q-date--dark {
          ${borderStyleCSS}
        }
      `
      document.head.appendChild(style)
    },
    setColors() {
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
      document.documentElement.style.setProperty(
        '--q-primary',
        LNbits.utils.getPaletteColor('primary')
      )
      document.documentElement.style.setProperty(
        '--q-secondary',
        LNbits.utils.getPaletteColor('secondary')
      )
    },
    copyText(text, message, position) {
      Quasar.copyToClipboard(text).then(() => {
        Quasar.Notify.create({
          message: message || 'Copied to clipboard!',
          position: position || 'bottom'
        })
      })
    },
    async checkUsrInUrl() {
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
    async logout() {
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
        const border = params.get('border')

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
        if (border) {
          this.$q.localStorage.set('lnbits.border', border)
        }

        // Remove processed parameters
        fields.forEach(param => params.delete(param))

        window.history.replaceState(null, null, url.pathname)
      }

      this.setColors()
    },
    refreshRoute() {
      const path = window.location.pathname
      console.log(path)

      this.$router.push('/temp').then(() => {
        this.$router.replace({path})
      })
    }
  },
  async created() {
    this.g.allowedThemes = window.allowedThemes ?? ['bitcoin']
    this.$q.dark.set(
      this.$q.localStorage.has('lnbits.darkMode')
        ? this.$q.localStorage.getItem('lnbits.darkMode')
        : true
    )
    this.changeTheme(this.themeChoice)
    this.applyBorder()
    if (this.$q.dark.isActive) {
      this.applyGradient()
    }
    this.applyBackgroundImage()

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

    if (window.user) {
      this.g.user = Vue.reactive(window.LNbits.map.user(window.user))
    }
    if (window.wallet) {
      this.g.wallet = Vue.reactive(window.LNbits.map.wallet(window.wallet))
    }
    if (window.extensions) {
      this.g.extensions = Vue.reactive(window.extensions)
    }
    await this.checkUsrInUrl()
    this.themeParams()
    this.walletFlip = this.$q.localStorage.getItem('lnbits.walletFlip')
    if (
      this.$q.screen.gt.sm ||
      this.$q.localStorage.getItem('lnbits.mobileSimple') == false
    ) {
      this.mobileSimple = false
    }
  },
  mounted() {
    if (this.g.user) {
      this.paymentEvents()
    }
  }
}

window.decryptLnurlPayAES = (success_action, preimage) => {
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
