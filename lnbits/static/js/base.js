/* globals crypto, moment, Vue, axios, Quasar, _ */

Vue.use(VueI18n)

window.LOCALE = 'en'
window.i18n = new VueI18n({
  locale: window.LOCALE,
  fallbackLocale: window.LOCALE,
  messages: window.localisation
})

window.EventHub = new Vue()
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
    createInvoice: async function (wallet, amount, memo, unit = 'sat') {
      return this.request('post', '/api/v1/payments', wallet.inkey, {
        out: false,
        amount: amount,
        memo: memo,
        unit: unit
      })
    },
    payInvoice: function (wallet, bolt11) {
      return this.request('post', '/api/v1/payments', wallet.adminkey, {
        out: true,
        bolt11: bolt11
      })
    },
    payLnurl: function (wallet, pay_response, amount, comment = '', unit = '') {
      return this.request('post', '/lnurl/api/v1/pay', wallet.adminkey, {
        pay_response,
        amount,
        comment,
        unit
      })
    },
    withdrawLnurl: function (wallet, withdraw_response, amount, memo) {
      return this.request('post', '/lnurl/api/v1/withdraw', wallet.adminkey, {
        withdraw_response,
        amount,
        memo
      })
    },
    authLnurl: function (wallet, auth_response) {
      return this.request('post', '/lnurl/api/v1/auth', wallet.adminkey, {
        auth_response
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
    login: function (username, password) {
      return axios({
        method: 'POST',
        url: '/api/v1/auth',
        data: {username, password}
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
      return LNbits.api.request('PUT', '/users/api/v1/topup', null, {
        amount: credit,
        id: wallet_id
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
      var obj = _.object(
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
      var obj = {
        id: data.id,
        admin: data.admin,
        email: data.email,
        extensions: data.extensions,
        wallets: data.wallets,
        admin: data.admin
      }
      var mapWallet = this.wallet
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
        pending: data.pending,
        amount: data.amount,
        fee: data.fee,
        memo: data.memo,
        time: data.time,
        bolt11: data.bolt11,
        preimage: data.preimage,
        payment_hash: data.payment_hash,
        expiry: data.expiry,
        extra: data.extra,
        wallet_id: data.wallet_id,
        webhook: data.webhook,
        webhook_status: data.webhook_status,
        fiat_amount: data.fiat_amount,
        fiat_currency: data.fiat_currency
      }

      obj.date = Quasar.utils.date.formatDate(
        new Date(obj.time * 1000),
        'YYYY-MM-DD HH:mm'
      )
      obj.dateFrom = moment(obj.date).fromNow()
      obj.expirydate = Quasar.utils.date.formatDate(
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
      obj.isPaid = !obj.pending
      obj._q = [obj.memo, obj.sat].join(' ').toLowerCase()
      return obj
    }
  },
  utils: {
    confirmDialog: function (msg) {
      return Quasar.plugins.Dialog.create({
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
      var types = {
        400: 'warning',
        401: 'warning',
        500: 'negative'
      }
      Quasar.plugins.Notify.create({
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
        var queries = q.toLowerCase().split(separator || ' ')
        return data.filter(function (obj) {
          var matches = 0
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
      var wrapCsvValue = function (val, formatFn) {
        var formatted = formatFn !== void 0 ? formatFn(val) : val

        formatted =
          formatted === void 0 || formatted === null ? '' : String(formatted)

        formatted = formatted.split('"').join('""')

        return `"${formatted}"`
      }

      var content = [
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

      var status = Quasar.utils.exportFile(
        `${fileName || 'table-export'}.csv`,
        content,
        'text/csv'
      )

      if (status !== true) {
        Quasar.plugins.Notify.create({
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
    }
  }
}

window.windowMixin = {
  i18n: window.i18n,
  data: function () {
    return {
      toggleSubs: true,
      reactionChoice: 'confettiBothSides',
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
    copyText: function (text, message, position) {
      var notify = this.$q.notify
      Quasar.utils.copyToClipboard(text).then(function () {
        notify({
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
      window.i18n.locale = locale
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

    if (window.user) {
      this.g.user = Object.freeze(window.LNbits.map.user(window.user))
    }
    if (window.wallet) {
      this.g.wallet = Object.freeze(window.LNbits.map.wallet(window.wallet))
    }
    if (window.extensions) {
      var user = this.g.user
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
