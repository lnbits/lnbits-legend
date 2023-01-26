/* globals crypto, moment, Vue, axios, Quasar, _ */

window.LOCALE = 'en'
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
        unit: unit,
        lnurl_callback: lnurlCallback
      })
    },
    payInvoice: function (wallet, bolt11) {
      return this.request('post', '/api/v1/payments', wallet.adminkey, {
        out: true,
        bolt11: bolt11
      })
    },
    payKeysend: function (
      wallet,
      pubkey,
      amount,
      comment = ''
    ) {
      return this.request('post', '/api/v1/payments/keysend', wallet.adminkey, {
        pubkey,
        amount,
        comment,
      })
    },
    payLnurl: function (
      wallet,
      callback,
      description_hash,
      amount,
      description = '',
      comment = ''
    ) {
      return this.request('post', '/api/v1/payments/lnurl', wallet.adminkey, {
        callback,
        description_hash,
        amount,
        comment,
        description
      })
    },
    authLnurl: function (wallet, callback) {
      return this.request('post', '/api/v1/lnurlauth', wallet.adminkey, {
        callback
      })
    },
    getWallet: function (wallet) {
      return this.request('get', '/api/v1/wallet', wallet.inkey)
    },
    getPayments: function (wallet) {
      return this.request('get', '/api/v1/payments', wallet.inkey)
    },
    getPayment: function (wallet, paymentHash) {
      return this.request(
        'get',
        '/api/v1/payments/' + paymentHash,
        wallet.inkey
      )
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
  href: {
    createWallet: function (walletName, userId) {
      window.location.href =
        '/wallet?' + (userId ? 'usr=' + userId + '&' : '') + 'nme=' + walletName
    },
    updateWallet: function (walletName, userId, walletId) {
      window.location.href = `/wallet?usr=${userId}&wal=${walletId}&nme=${walletName}`
    },
    deleteWallet: function (walletId, userId) {
      window.location.href = '/deletewallet?usr=' + userId + '&wal=' + walletId
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
        inkey: data.inkey
      }
      newWallet.msat = data.balance_msat
      newWallet.sat = Math.round(data.balance_msat / 1000)
      newWallet.fsat = new Intl.NumberFormat(window.LOCALE).format(
        newWallet.sat
      )
      newWallet.url = ['/wallet?usr=', data.user, '&wal=', data.id].join('')
      return newWallet
    },
    payment: function (data) {
      obj = {
        checking_id: data.id,
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
        webhook_status: data.webhook_status
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
      obj.tag = obj.extra.tag
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
    formatCurrency: function (value, currency) {
      return new Intl.NumberFormat(window.LOCALE, {
        style: 'currency',
        currency: currency
      }).format(value)
    },
    formatSat: function (value) {
      return new Intl.NumberFormat(window.LOCALE).format(value)
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
    }
  }
}

window.windowMixin = {
  data: function () {
    return {
      g: {
        offline: !navigator.onLine,
        visibleDrawer: false,
        extensions: [],
        user: null,
        wallet: null,
        payments: [],
        allowedThemes: null
      }
    }
  },

  methods: {
    changeColor: function (newValue) {
      document.body.setAttribute('data-theme', newValue)
      this.$q.localStorage.set('lnbits.theme', newValue)
    },
    toggleDarkMode: function () {
      this.$q.dark.toggle()
      this.$q.localStorage.set('lnbits.darkMode', this.$q.dark.isActive)
    },
    copyText: function (text, message, position) {
      var notify = this.$q.notify
      Quasar.utils.copyToClipboard(text).then(function () {
        notify({
          message: message || 'Copied to clipboard!',
          position: position || 'bottom'
        })
      })
    }
  },
  created: function () {
    if (
      this.$q.localStorage.getItem('lnbits.darkMode') == true ||
      this.$q.localStorage.getItem('lnbits.darkMode') == false
    ) {
      this.$q.dark.set(this.$q.localStorage.getItem('lnbits.darkMode'))
    } else {
      this.$q.dark.set(true)
    }
    this.g.allowedThemes = window.allowedThemes ?? ['bitcoin']

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
            if (window.user.admin) return obj
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
