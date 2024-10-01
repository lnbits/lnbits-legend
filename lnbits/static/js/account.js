window.app = Vue.createApp({
  el: '#vue',
  mixins: [window.windowMixin],
  data: function () {
    return {
      user: null,
      hasUsername: false,
      showUserId: false,
      reactionOptions: [
        'None',
        'confettiBothSides',
        'confettiFireworks',
        'confettiStars'
      ],
      tab: 'user',
      credentialsData: {
        show: false,
        oldPassword: null,
        newPassword: null,
        newPasswordRepeat: null,
        username: null,
        pubkey: null
      }
    }
  },
  methods: {
    activeLanguage: function (lang) {
      return window.i18n.global.locale === lang
    },
    changeLanguage: function (newValue) {
      window.i18n.global.locale = newValue
      this.$q.localStorage.set('lnbits.lang', newValue)
    },
    toggleDarkMode: function () {
      this.$q.dark.toggle()
      this.$q.localStorage.set('lnbits.darkMode', this.$q.dark.isActive)
      if (!this.$q.dark.isActive && this.gradientChoice) {
        this.toggleGradient()
      }
    },
    applyGradient: function () {
      darkBgColor = this.$q.localStorage.getItem('lnbits.darkBgColor')
      primaryColor = this.$q.localStorage.getItem('lnbits.primaryColor')
      if (this.gradientChoice) {
        if (!this.$q.dark.isActive) {
          this.toggleDarkMode()
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
          `body[data-theme="${this.$q.localStorage.getItem('lnbits.theme')}"] .q-card:not(.q-dialog .q-card, .lnbits__dialog-card, .q-dialog-plugin--dark), body.body${this.$q.dark.isActive ? '--dark' : ''} .q-header, body.body${this.$q.dark.isActive ? '--dark' : ''} .q-drawer { ${gradientStyleCards} }` +
          `body[data-theme="${this.$q.localStorage.getItem('lnbits.theme')}"].body--dark{background: ${LNbits.utils.hexDarken(String(primaryColor), -88)} !important; }` +
          `[data-theme="${this.$q.localStorage.getItem('lnbits.theme')}"] .q-card--dark{background: ${String(darkBgColor)} !important;} }`
        document.head.appendChild(style)
        this.$q.localStorage.set('lnbits.gradientBg', true)
      } else {
        this.$q.localStorage.set('lnbits.gradientBg', false)
      }
    },
    toggleGradient: function () {
      this.gradientChoice = !this.gradientChoice
      this.applyGradient()
      if (!this.gradientChoice) {
        window.location.reload()
      }
      this.gradientChoice = this.$q.localStorage.getItem('lnbits.gradientBg')
    },
    reactionChoiceFunc: function () {
      this.$q.localStorage.set('lnbits.reactions', this.reactionChoice)
    },
    changeColor: function (newValue) {
      document.body.setAttribute('data-theme', newValue)
      this.$q.localStorage.set('lnbits.theme', newValue)
      this.setColors()
      if (this.$q.localStorage.getItem('lnbits.gradientBg')) {
        this.applyGradient()
      }
    },
    updateAccount: async function () {
      try {
        const {data} = await LNbits.api.request(
          'PUT',
          '/api/v1/auth/update',
          null,
          {
            user_id: this.user.id,
            username: this.user.username,
            email: this.user.email,
            extra: this.user.extra
          }
        )
        this.user = data
        this.hasUsername = !!data.username
        Quasar.Notify.create({
          type: 'positive',
          message: 'Account updated.'
        })
      } catch (e) {
        LNbits.utils.notifyApiError(e)
      }
    },
    disableUpdatePassword: function () {
      return (
        !this.credentialsData.newPassword ||
        !this.credentialsData.newPasswordRepeat ||
        this.credentialsData.newPassword !==
          this.credentialsData.newPasswordRepeat
      )
    },
    updatePassword: async function () {
      if (!this.credentialsData.username) {
        Quasar.Notify.create({
          type: 'warning',
          message: 'Please set a username.'
        })
        return
      }
      try {
        const {data} = await LNbits.api.request(
          'PUT',
          '/api/v1/auth/password',
          null,
          {
            user_id: this.user.id,
            username: this.credentialsData.username,
            password_old: this.credentialsData.oldPassword,
            password: this.credentialsData.newPassword,
            password_repeat: this.credentialsData.newPasswordRepeat
          }
        )
        this.user = data
        this.hasUsername = !!data.username
        this.credentialsData.show = false
        Quasar.Notify.create({
          type: 'positive',
          message: 'Password updated.'
        })
      } catch (e) {
        LNbits.utils.notifyApiError(e)
      }
    },
    updatePubkey: async function () {
      try {
        const {data} = await LNbits.api.request(
          'PUT',
          '/api/v1/auth/pubkey',
          null,
          {
            user_id: this.user.id,
            pubkey: this.credentialsData.pubkey
          }
        )
        this.user = data
        this.hasUsername = !!data.username
        this.credentialsData.show = false
        this.$q.notify({
          type: 'positive',
          message: 'Public key updated.'
        })
      } catch (e) {
        LNbits.utils.notifyApiError(e)
      }
    },
    showUpdateCredentials: function () {
      this.credentialsData = {
        show: true,
        oldPassword: null,
        username: this.user.username,
        pubkey: this.user.pubkey,
        newPassword: null,
        newPasswordRepeat: null
      }
    }
  },
  created: async function () {
    try {
      const {data} = await LNbits.api.getAuthenticatedUser()
      this.user = data
      this.hasUsername = !!data.username
      if (!this.user.extra) this.user.extra = {}
    } catch (e) {
      LNbits.utils.notifyApiError(e)
    }
    if (this.$q.localStorage.getItem('lnbits.gradientBg')) {
      this.applyGradient()
    }
  }
})
