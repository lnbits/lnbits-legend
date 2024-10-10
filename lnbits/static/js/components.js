window.app.component(QrcodeVue)

window.app.component('lnbits-extension-rating', {
  template: '#lnbits-extension-rating',
  name: 'lnbits-extension-rating',
  props: ['rating']
})

window.app.component('lnbits-fsat', {
  template: '<span>{{ fsat }}</span>',
  props: {
    amount: {
      type: Number,
      default: 0
    }
  },
  computed: {
    fsat: function () {
      return LNbits.utils.formatSat(this.amount)
    }
  }
})

window.app.component('lnbits-wallet-list', {
  template: '#lnbits-wallet-list',
  props: ['balance'],
  data: function () {
    return {
      user: null,
      activeWallet: null,
      balance: 0,
      showForm: false,
      walletName: '',
      LNBITS_DENOMINATION: LNBITS_DENOMINATION
    }
  },
  computed: {
    wallets: function () {
      var bal = this.balance
      return this.user.wallets.map(function (obj) {
        obj.live_fsat =
          bal.length && bal[0] === obj.id
            ? LNbits.utils.formatSat(bal[1])
            : obj.fsat
        return obj
      })
    }
  },
  methods: {
    createWallet: function () {
      LNbits.api.createWallet(this.user.wallets[0], this.walletName)
    }
  },
  created: function () {
    if (window.user) {
      this.user = LNbits.map.user(window.user)
    }
    if (window.wallet) {
      this.activeWallet = LNbits.map.wallet(window.wallet)
    }
    document.addEventListener('updateWalletBalance', this.updateWalletBalance)
  }
})

window.app.component('lnbits-extension-list', {
  template: '#lnbits-extension-list',
  data: function () {
    return {
      extensions: [],
      user: null
    }
  },
  computed: {
    userExtensions: function () {
      if (!this.user) return []

      var path = window.location.pathname
      var userExtensions = this.user.extensions

      return this.extensions
        .filter(function (obj) {
          return userExtensions.indexOf(obj.code) !== -1
        })
        .map(function (obj) {
          obj.isActive = path.startsWith(obj.url)
          return obj
        })
    }
  },
  created: function () {
    if (window.extensions) {
      this.extensions = window.extensions
        .map(function (data) {
          return LNbits.map.extension(data)
        })
        .sort(function (a, b) {
          return a.name.localeCompare(b.name)
        })
    }

    if (window.user) {
      this.user = LNbits.map.user(window.user)
    }
  }
})

window.app.component('lnbits-manage', {
  template: '#lnbits-manage',
  props: ['showAdmin', 'showNode', 'showExtensions', 'showUsers'],
  methods: {
    isActive: function (path) {
      return window.location.pathname === path
    }
  },
  data() {
    return {
      extensions: [],
      user: null
    }
  },
  created() {
    if (window.user) {
      this.user = LNbits.map.user(window.user)
    }
  }
})

window.app.component('lnbits-payment-details', {
  template: '#lnbits-payment-details',
  props: ['payment'],
  mixins: [window.windowMixin],
  data: function () {
    return {
      LNBITS_DENOMINATION: LNBITS_DENOMINATION
    }
  },
  computed: {
    hasPreimage() {
      return (
        this.payment.preimage &&
        this.payment.preimage !==
          '0000000000000000000000000000000000000000000000000000000000000000'
      )
    },
    hasExpiry() {
      return !!this.payment.expiry
    },
    hasSuccessAction() {
      return (
        this.hasPreimage &&
        this.payment.extra &&
        this.payment.extra.success_action
      )
    },
    webhookStatusColor() {
      return this.payment.webhook_status >= 300 ||
        this.payment.webhook_status < 0
        ? 'red-10'
        : !this.payment.webhook_status
          ? 'cyan-7'
          : 'green-10'
    },
    webhookStatusText() {
      return this.payment.webhook_status
        ? this.payment.webhook_status
        : 'not sent yet'
    },
    hasTag() {
      return this.payment.extra && !!this.payment.extra.tag
    },
    extras() {
      if (!this.payment.extra) return []
      let extras = _.omit(this.payment.extra, ['tag', 'success_action'])
      return Object.keys(extras).map(key => ({key, value: extras[key]}))
    }
  }
})

window.app.component('lnbits-lnurlpay-success-action', {
  template: '#lnbits-lnurlpay-success-action',
  props: ['payment', 'success_action'],
  data() {
    return {
      decryptedValue: this.success_action.ciphertext
    }
  },
  mounted: function () {
    if (this.success_action.tag !== 'aes') return null
    decryptLnurlPayAES(this.success_action, this.payment.preimage).then(
      value => {
        this.decryptedValue = value
      }
    )
  }
})

window.app.component('lnbits-qrcode', {
  template: '#lnbits-qrcode',
  mixins: [window.windowMixin],
  components: {
    QrcodeVue
  },
  props: ['value'],
  data() {
    return {
      logo: LNBITS_QR_LOGO
    }
  }
})

window.app.component('lnbits-notifications-btn', {
  template: '#lnbits-notifications-btn',
  mixins: [window.windowMixin],
  props: ['pubkey'],
  data() {
    return {
      isSupported: false,
      isSubscribed: false,
      isPermissionGranted: false,
      isPermissionDenied: false
    }
  },
  methods: {
    // converts base64 to Array buffer
    urlB64ToUint8Array(base64String) {
      const padding = '='.repeat((4 - (base64String.length % 4)) % 4)
      const base64 = (base64String + padding)
        .replace(/\-/g, '+')
        .replace(/_/g, '/')
      const rawData = atob(base64)
      const outputArray = new Uint8Array(rawData.length)

      for (let i = 0; i < rawData.length; ++i) {
        outputArray[i] = rawData.charCodeAt(i)
      }

      return outputArray
    },
    toggleNotifications() {
      this.isSubscribed ? this.unsubscribe() : this.subscribe()
    },
    saveUserSubscribed(user) {
      let subscribedUsers =
        JSON.parse(
          this.$q.localStorage.getItem('lnbits.webpush.subscribedUsers')
        ) || []
      if (!subscribedUsers.includes(user)) subscribedUsers.push(user)
      this.$q.localStorage.set(
        'lnbits.webpush.subscribedUsers',
        JSON.stringify(subscribedUsers)
      )
    },
    removeUserSubscribed(user) {
      let subscribedUsers =
        JSON.parse(
          this.$q.localStorage.getItem('lnbits.webpush.subscribedUsers')
        ) || []
      subscribedUsers = subscribedUsers.filter(arr => arr !== user)
      this.$q.localStorage.set(
        'lnbits.webpush.subscribedUsers',
        JSON.stringify(subscribedUsers)
      )
    },
    isUserSubscribed(user) {
      let subscribedUsers =
        JSON.parse(
          this.$q.localStorage.getItem('lnbits.webpush.subscribedUsers')
        ) || []
      return subscribedUsers.includes(user)
    },
    subscribe() {
      var self = this

      // catch clicks from disabled type='a' button (https://github.com/quasarframework/quasar/issues/9258)
      if (!this.isSupported || this.isPermissionDenied) {
        return
      }

      // ask for notification permission
      Notification.requestPermission()
        .then(permission => {
          this.isPermissionGranted = permission === 'granted'
          this.isPermissionDenied = permission === 'denied'
        })
        .catch(function (e) {
          console.log(e)
        })

      // create push subscription
      navigator.serviceWorker.ready.then(registration => {
        navigator.serviceWorker.getRegistration().then(registration => {
          registration.pushManager
            .getSubscription()
            .then(function (subscription) {
              if (
                subscription === null ||
                !self.isUserSubscribed(self.g.user.id)
              ) {
                const applicationServerKey = self.urlB64ToUint8Array(
                  self.pubkey
                )
                const options = {applicationServerKey, userVisibleOnly: true}

                registration.pushManager
                  .subscribe(options)
                  .then(function (subscription) {
                    LNbits.api
                      .request(
                        'POST',
                        '/api/v1/webpush',
                        self.g.user.wallets[0].adminkey,
                        {
                          subscription: JSON.stringify(subscription)
                        }
                      )
                      .then(function (response) {
                        self.saveUserSubscribed(response.data.user)
                        self.isSubscribed = true
                      })
                      .catch(function (error) {
                        LNbits.utils.notifyApiError(error)
                      })
                  })
              }
            })
            .catch(function (e) {
              console.log(e)
            })
        })
      })
    },
    unsubscribe() {
      var self = this

      navigator.serviceWorker.ready
        .then(registration => {
          registration.pushManager.getSubscription().then(subscription => {
            if (subscription) {
              LNbits.api
                .request(
                  'DELETE',
                  '/api/v1/webpush?endpoint=' + btoa(subscription.endpoint),
                  self.g.user.wallets[0].adminkey
                )
                .then(function () {
                  self.removeUserSubscribed(self.g.user.id)
                  self.isSubscribed = false
                })
                .catch(function (error) {
                  LNbits.utils.notifyApiError(error)
                })
            }
          })
        })
        .catch(function (e) {
          console.log(e)
        })
    },
    checkSupported: function () {
      let https = window.location.protocol === 'https:'
      let serviceWorkerApi = 'serviceWorker' in navigator
      let notificationApi = 'Notification' in window
      let pushApi = 'PushManager' in window

      this.isSupported = https && serviceWorkerApi && notificationApi && pushApi

      if (!this.isSupported) {
        console.log(
          'Notifications disabled because requirements are not met:',
          {
            HTTPS: https,
            'Service Worker API': serviceWorkerApi,
            'Notification API': notificationApi,
            'Push API': pushApi
          }
        )
      }

      return this.isSupported
    },
    updateSubscriptionStatus: async function () {
      var self = this

      await navigator.serviceWorker.ready
        .then(registration => {
          registration.pushManager.getSubscription().then(subscription => {
            self.isSubscribed =
              !!subscription && self.isUserSubscribed(self.g.user.id)
          })
        })
        .catch(function (e) {
          console.log(e)
        })
    }
  },
  created: function () {
    this.isPermissionDenied = Notification.permission === 'denied'

    if (this.checkSupported()) {
      this.updateSubscriptionStatus()
    }
  }
})

window.app.component('lnbits-dynamic-fields', {
  template: '#lnbits-dynamic-fields',
  mixins: [window.windowMixin],
  props: ['options', 'modelValue'],
  data() {
    return {
      formData: null,
      rules: [val => !!val || 'Field is required']
    }
  },
  methods: {
    applyRules(required) {
      return required ? this.rules : []
    },
    buildData(options, data = {}) {
      return options.reduce((d, option) => {
        if (option.options?.length) {
          d[option.name] = this.buildData(option.options, data[option.name])
        } else {
          d[option.name] = data[option.name] ?? option.default
        }
        return d
      }, {})
    },
    handleValueChanged() {
      this.$emit('update:model-value', this.formData)
    }
  },
  created() {
    this.formData = this.buildData(this.options, this.modelValue)
  }
})

window.app.component('lnbits-dynamic-chips', {
  template: '#lnbits-dynamic-chips',
  mixins: [window.windowMixin],
  props: ['modelValue'],
  data() {
    return {
      chip: '',
      chips: []
    }
  },
  methods: {
    addChip() {
      if (!this.chip) return
      this.chips.push(this.chip)
      this.chip = ''
      this.$emit('update:model-value', this.chips.join(','))
    },
    removeChip(index) {
      this.chips.splice(index, 1)
      this.$emit('update:model-value', this.chips.join(','))
    }
  },
  created() {
    if (typeof this.modelValue === 'string') {
      this.chips = this.modelValue.split(',')
    } else {
      this.chips = [...this.modelValue]
    }
  }
})

window.app.component('lnbits-update-balance', {
  template: '#lnbits-update-balance',
  mixins: [window.windowMixin],
  props: ['wallet_id', 'callback'],
  computed: {
    denomination() {
      return LNBITS_DENOMINATION
    },
    admin() {
      return user.super_user
    }
  },
  data: function () {
    return {
      credit: 0
    }
  },
  methods: {
    updateBalance: function (credit) {
      LNbits.api
        .updateBalance(credit, this.wallet_id)
        .then(res => {
          if (res.data.status !== 'Success') {
            throw new Error(res.data)
          }
          this.callback({
            success: true,
            credit: parseInt(credit),
            wallet_id: this.wallet_id
          })
        })
        .then(_ => {
          credit = parseInt(credit)
          Quasar.Notify.create({
            type: 'positive',
            message: this.$t('wallet_topup_ok', {
              amount: credit
            }),
            icon: null
          })
          return credit
        })
        .catch(function (error) {
          LNbits.utils.notifyApiError(error)
        })
    }
  }
})
