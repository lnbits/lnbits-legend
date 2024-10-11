window.app = Vue.createApp({
  el: '#vue',
  mixins: [window.windowMixin],
  data: function () {
    return {
      updatePayments: false,
      origin: window.location.origin,
      wallet: LNbits.map.wallet(window.wallet),
      user: LNbits.map.user(window.user),
      exportUrl: `${window.location.origin}/wallet?usr=${window.user.id}&wal=${window.wallet.id}`,
      receive: {
        show: false,
        status: 'pending',
        paymentReq: null,
        paymentHash: null,
        minMax: [0, 2100000000000000],
        lnurl: null,
        units: ['sat'],
        unit: 'sat',
        data: {
          amount: null,
          memo: ''
        }
      },
      parse: {
        show: false,
        invoice: null,
        lnurlpay: null,
        lnurlauth: null,
        data: {
          request: '',
          amount: 0,
          comment: '',
          unit: 'sat'
        },
        paymentChecker: null,
        copy: {
          show: false
        },
        camera: {
          show: false,
          camera: 'auto'
        }
      },
      disclaimerDialog: {
        show: false,
        location: window.location
      },
      balance: parseInt(wallet.balance_msat / 1000),
      fiatBalance: 0,
      mobileSimple: false,
      credit: 0,
      update: {
        name: null,
        currency: null
      },
      inkeyHidden: true,
      adminkeyHidden: true
    }
  },
  computed: {
    formattedBalance: function () {
      if (LNBITS_DENOMINATION != 'sats') {
        return this.balance / 100
      } else {
        return LNbits.utils.formatSat(this.balance || this.g.wallet.sat)
      }
    },
    formattedFiatBalance() {
      if (this.fiatBalance) {
        return LNbits.utils.formatCurrency(
          this.fiatBalance.toFixed(2),
          this.g.wallet.currency
        )
      }
    },
    canPay: function () {
      if (!this.parse.invoice) return false
      return this.parse.invoice.sat <= this.balance
    }
  },
  methods: {
    msatoshiFormat: function (value) {
      return LNbits.utils.formatSat(value / 1000)
    },
    closeCamera: function () {
      this.parse.camera.show = false
    },
    showCamera: function () {
      this.parse.camera.show = true
    },
    focusInput(el) {
      this.$nextTick(() => this.$refs[el].focus())
    },
    showReceiveDialog: function () {
      this.receive.show = true
      this.receive.status = 'pending'
      this.receive.paymentReq = null
      this.receive.paymentHash = null
      this.receive.data.amount = null
      this.receive.data.memo = null
      this.receive.unit = 'sat'
      this.receive.minMax = [0, 2100000000000000]
      this.receive.lnurl = null
      this.focusInput('setAmount')
    },
    showParseDialog: function () {
      this.parse.show = true
      this.parse.invoice = null
      this.parse.lnurlpay = null
      this.parse.lnurlauth = null
      this.parse.copy.show =
        window.isSecureContext && navigator.clipboard?.readText !== undefined
      this.parse.data.request = ''
      this.parse.data.comment = ''
      this.parse.data.paymentChecker = null
      this.parse.camera.show = false
      this.focusInput('textArea')
    },
    closeParseDialog: function () {
      setTimeout(() => {
        clearInterval(this.parse.paymentChecker)
      }, 10000)
    },
    onPaymentReceived: function (paymentHash) {
      this.updatePayments = !this.updatePayments
      if (this.receive.paymentHash === paymentHash) {
        this.receive.show = false
        this.receive.paymentHash = null
      }
    },
    createInvoice: function () {
      this.receive.status = 'loading'
      if (LNBITS_DENOMINATION != 'sats') {
        this.receive.data.amount = this.receive.data.amount * 100
      }
      LNbits.api
        .createInvoice(
          this.g.wallet,
          this.receive.data.amount,
          this.receive.data.memo,
          this.receive.unit,
          this.receive.lnurl && this.receive.lnurl.callback
        )
        .then(response => {
          this.receive.status = 'success'
          this.receive.paymentReq = response.data.payment_request
          this.receive.paymentHash = response.data.payment_hash

          if (response.data.lnurl_response !== null) {
            if (response.data.lnurl_response === false) {
              response.data.lnurl_response = `Unable to connect`
            }

            if (typeof response.data.lnurl_response === 'string') {
              // failure
              Quasar.Notify.create({
                timeout: 5000,
                type: 'warning',
                message: `${this.receive.lnurl.domain} lnurl-withdraw call failed.`,
                caption: response.data.lnurl_response
              })
              return
            } else if (response.data.lnurl_response === true) {
              // success
              Quasar.Notify.create({
                timeout: 5000,
                message: `Invoice sent to ${this.receive.lnurl.domain}!`,
                spinner: true
              })
            }
          }
        })
        .then(() => {
          this.updatePayments = !this.updatePayments
        })
        .catch(err => {
          LNbits.utils.notifyApiError(err)
          this.receive.status = 'pending'
        })
    },
    onInitQR: async function (promise) {
      try {
        await promise
      } catch (error) {
        let mapping = {
          NotAllowedError: 'ERROR: you need to grant camera access permission',
          NotFoundError: 'ERROR: no camera on this device',
          NotSupportedError:
            'ERROR: secure context required (HTTPS, localhost)',
          NotReadableError: 'ERROR: is the camera already in use?',
          OverconstrainedError: 'ERROR: installed cameras are not suitable',
          StreamApiNotSupportedError:
            'ERROR: Stream API is not supported in this browser',
          InsecureContextError:
            'ERROR: Camera access is only permitted in secure context. Use HTTPS or localhost rather than HTTP.'
        }
        let valid_error = Object.keys(mapping).filter(key => {
          return error.name === key
        })
        let camera_error = valid_error
          ? mapping[valid_error]
          : `ERROR: Camera error (${error.name})`
        this.parse.camera.show = false
        Quasar.Notify.create({
          message: camera_error,
          type: 'negative'
        })
      }
    },
    lnurlScan() {
      LNbits.api
        .request(
          'GET',
          '/api/v1/lnurlscan/' + this.parse.data.request,
          this.g.wallet.adminkey
        )
        .catch(err => {
          LNbits.utils.notifyApiError(err)
        })
        .then(response => {
          let data = response.data

          if (data.status === 'ERROR') {
            Quasar.Notify.create({
              timeout: 5000,
              type: 'warning',
              message: `${data.domain} lnurl call failed.`,
              caption: data.reason
            })
            return
          }

          if (data.kind === 'pay') {
            this.parse.lnurlpay = Object.freeze(data)
            this.parse.data.amount = data.minSendable / 1000
          } else if (data.kind === 'auth') {
            this.parse.lnurlauth = Object.freeze(data)
          } else if (data.kind === 'withdraw') {
            this.parse.show = false
            this.receive.show = true
            this.receive.status = 'pending'
            this.receive.paymentReq = null
            this.receive.paymentHash = null
            this.receive.data.amount = data.maxWithdrawable / 1000
            this.receive.data.memo = data.defaultDescription
            this.receive.minMax = [
              data.minWithdrawable / 1000,
              data.maxWithdrawable / 1000
            ]
            this.receive.lnurl = {
              domain: data.domain,
              callback: data.callback,
              fixed: data.fixed
            }
          }
        })
    },
    decodeQR: function (res) {
      this.parse.data.request = res[0].rawValue
      this.decodeRequest()
      this.parse.camera.show = false
    },
    decodeRequest: function () {
      this.parse.show = true
      this.parse.data.request = this.parse.data.request.trim().toLowerCase()
      let req = this.parse.data.request
      if (req.startsWith('lightning:')) {
        this.parse.data.request = req.slice(10)
      } else if (req.startsWith('lnurl:')) {
        this.parse.data.request = req.slice(6)
      } else if (req.includes('lightning=lnurl1')) {
        this.parse.data.request = req.split('lightning=')[1].split('&')[0]
      }
      req = this.parse.data.request
      if (req.startsWith('lnurl1') || req.match(/[\w.+-~_]+@[\w.+-~_]/)) {
        this.lnurlScan()
        return
      }

      // BIP-21 support
      if (this.parse.data.request.toLowerCase().includes('lightning')) {
        this.parse.data.request = this.parse.data.request.split('lightning=')[1]

        // fail safe to check there's nothing after the lightning= part
        if (this.parse.data.request.includes('&')) {
          this.parse.data.request = this.parse.data.request.split('&')[0]
        }
      }

      let invoice
      try {
        invoice = decode(this.parse.data.request)
      } catch (error) {
        Quasar.Notify.create({
          timeout: 3000,
          type: 'warning',
          message: error + '.',
          caption: '400 BAD REQUEST'
        })
        this.parse.show = false
        return
      }

      let cleanInvoice = {
        msat: invoice.human_readable_part.amount,
        sat: invoice.human_readable_part.amount / 1000,
        fsat: LNbits.utils.formatSat(invoice.human_readable_part.amount / 1000)
      }

      _.each(invoice.data.tags, tag => {
        if (_.isObject(tag) && _.has(tag, 'description')) {
          if (tag.description === 'payment_hash') {
            cleanInvoice.hash = tag.value
          } else if (tag.description === 'description') {
            cleanInvoice.description = tag.value
          } else if (tag.description === 'expiry') {
            var expireDate = new Date(
              (invoice.data.time_stamp + tag.value) * 1000
            )
            cleanInvoice.expireDate = Quasar.date.formatDate(
              expireDate,
              'YYYY-MM-DDTHH:mm:ss.SSSZ'
            )
            cleanInvoice.expired = false // TODO
          }
        }
      })

      this.parse.invoice = Object.freeze(cleanInvoice)
    },
    payInvoice: function () {
      let dismissPaymentMsg = Quasar.Notify.create({
        timeout: 0,
        message: this.$t('processing_payment')
      })

      LNbits.api
        .payInvoice(this.g.wallet, this.parse.data.request)
        .then(response => {
          clearInterval(this.parse.paymentChecker)
          setTimeout(() => {
            clearInterval(this.parse.paymentChecker)
          }, 40000)
          this.parse.paymentChecker = setInterval(() => {
            LNbits.api
              .getPayment(this.g.wallet, response.data.payment_hash)
              .then(res => {
                if (res.data.paid) {
                  dismissPaymentMsg()
                  clearInterval(this.parse.paymentChecker)
                  this.updatePayments = !this.updatePayments
                  this.parse.show = false
                }
              })
          }, 2000)
        })
        .catch(err => {
          dismissPaymentMsg()
          LNbits.utils.notifyApiError(err)
          this.updatePayments = !this.updatePayments
          this.parse.show = false
        })
    },
    payLnurl: function () {
      let dismissPaymentMsg = Quasar.Notify.create({
        timeout: 0,
        message: 'Processing payment...'
      })

      LNbits.api
        .payLnurl(
          this.g.wallet,
          this.parse.lnurlpay.callback,
          this.parse.lnurlpay.description_hash,
          this.parse.data.amount * 1000,
          this.parse.lnurlpay.description.slice(0, 120),
          this.parse.data.comment,
          this.parse.data.unit
        )
        .then(response => {
          this.parse.show = false

          clearInterval(this.parse.paymentChecker)
          setTimeout(() => {
            clearInterval(this.parse.paymentChecker)
          }, 40000)
          this.parse.paymentChecker = setInterval(() => {
            LNbits.api
              .getPayment(this.g.wallet, response.data.payment_hash)
              .then(res => {
                if (res.data.paid) {
                  dismissPaymentMsg()
                  clearInterval(this.parse.paymentChecker)
                  // show lnurlpay success action
                  if (response.data.success_action) {
                    switch (response.data.success_action.tag) {
                      case 'url':
                        Quasar.Notify.create({
                          message: `<a target="_blank" style="color: inherit" href="${response.data.success_action.url}">${response.data.success_action.url}</a>`,
                          caption: response.data.success_action.description,
                          html: true,
                          type: 'positive',
                          timeout: 0,
                          closeBtn: true
                        })
                        break
                      case 'message':
                        Quasar.Notify.create({
                          message: response.data.success_action.message,
                          type: 'positive',
                          timeout: 0,
                          closeBtn: true
                        })
                        break
                      case 'aes':
                        LNbits.api
                          .getPayment(this.g.wallet, response.data.payment_hash)
                          .then(({data: payment}) =>
                            decryptLnurlPayAES(
                              response.data.success_action,
                              payment.preimage
                            )
                          )
                          .then(value => {
                            Quasar.Notify.create({
                              message: value,
                              caption: response.data.success_action.description,
                              html: true,
                              type: 'positive',
                              timeout: 0,
                              closeBtn: true
                            })
                          })
                        break
                    }
                  }
                }
              })
          }, 2000)
        })
        .catch(err => {
          dismissPaymentMsg()
          LNbits.utils.notifyApiError(err)
        })
    },
    authLnurl: function () {
      let dismissAuthMsg = Quasar.Notify.create({
        timeout: 10,
        message: 'Performing authentication...'
      })

      LNbits.api
        .authLnurl(this.g.wallet, this.parse.lnurlauth.callback)
        .then(_ => {
          dismissAuthMsg()
          Quasar.Notify.create({
            message: `Authentication successful.`,
            type: 'positive',
            timeout: 3500
          })
          this.parse.show = false
        })
        .catch(err => {
          dismissAuthMsg()
          if (err.response.data.reason) {
            Quasar.Notify.create({
              message: `Authentication failed. ${this.parse.lnurlauth.domain} says:`,
              caption: err.response.data.reason,
              type: 'warning',
              timeout: 5000
            })
          } else {
            LNbits.utils.notifyApiError(err)
          }
        })
    },
    updateWallet: function (data) {
      LNbits.api
        .request('PATCH', '/api/v1/wallet', this.g.wallet.adminkey, data)
        .then(_ => {
          Quasar.Notify.create({
            message: `Wallet updated.`,
            type: 'positive',
            timeout: 3500
          })
          window.location.reload()
        })
        .catch(err => {
          LNbits.utils.notifyApiError(err)
        })
    },
    deleteWallet: function () {
      LNbits.utils
        .confirmDialog('Are you sure you want to delete this wallet?')
        .onOk(() => {
          LNbits.api
            .deleteWallet(this.g.wallet)
            .then(_ => {
              Quasar.Notify.create({
                timeout: 3000,
                message: `Wallet deleted!`,
                spinner: true
              })
            })
            .catch(err => {
              LNbits.utils.notifyApiError(err)
            })
        })
    },
    fetchBalance: function () {
      LNbits.api.getWallet(this.g.wallet).then(response => {
        this.balance = Math.floor(response.data.balance / 1000)
        document.dispatchEvent(
          new CustomEvent('updateWalletBalance', {
            detail: [this.g.wallet.id, this.balance]
          })
        )
      })
      if (this.g.wallet.currency) {
        this.updateFiatBalance()
      }
    },
    updateFiatBalance() {
      if (!this.g.wallet.currency) return 0
      LNbits.api
        .request('POST', `/api/v1/conversion`, null, {
          amount: this.balance || this.g.wallet.sat,
          to: this.g.wallet.currency
        })
        .then(response => {
          this.fiatBalance = response.data[this.g.wallet.currency]
        })
        .catch(e => console.error(e))
    },
    updateBalanceCallback: function (res) {
      if (res.success && wallet.id === res.wallet_id) {
        this.balance += res.credit
      }
    },
    pasteToTextArea: function () {
      this.$refs.textArea.focus() // Set cursor to textarea
      navigator.clipboard.readText().then(text => {
        this.parse.data.request = text.trim()
      })
    }
  },
  created: function () {
    let urlParams = new URLSearchParams(window.location.search)
    if (urlParams.has('lightning') || urlParams.has('lnurl')) {
      this.parse.data.request =
        urlParams.get('lightning') || urlParams.get('lnurl')
      this.decodeRequest()
      this.parse.show = true
    }
    if (this.$q.screen.lt.md) {
      this.mobileSimple = true
    }
    this.update.name = this.g.wallet.name
    this.update.currency = this.g.wallet.currency
    this.receive.units = ['sat', ...window.currencies]
    this.updateFiatBalance()
  },
  watch: {
    updatePayments: function () {
      this.fetchBalance()
    }
  },
  mounted: function () {
    // show disclaimer
    if (!this.$q.localStorage.getItem('lnbits.disclaimerShown')) {
      this.disclaimerDialog.show = true
      this.$q.localStorage.set('lnbits.disclaimerShown', true)
    }
    // listen to incoming payments
    LNbits.events.onInvoicePaid(this.g.wallet, payment => {
      this.onPaymentReceived(payment.payment_hash)
    })
    eventReactionWebocket(wallet.id)
  }
})

if (navigator.serviceWorker != null) {
  navigator.serviceWorker
    .register('/service-worker.js')
    .then(function (registration) {
      console.log('Registered events at scope: ', registration.scope)
    })
}
