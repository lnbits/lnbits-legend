window.app = Vue.createApp({
  el: '#vue',
  mixins: [windowMixin],
  data: function () {
    return {
      settings: {},
      logs: [],
      serverlogEnabled: false,
      lnbits_theme_options: [
        'classic',
        'bitcoin',
        'flamingo',
        'cyber',
        'freedom',
        'mint',
        'autumn',
        'monochrome',
        'salvador'
      ],
      auditData: {},
      statusData: {},
      statusDataTable: {
        columns: [
          {
            name: 'date',
            align: 'left',
            label: this.$t('date'),
            field: 'date'
          },
          {
            name: 'message',
            align: 'left',
            label: this.$t('memo'),
            field: 'message'
          }
        ]
      },
      formData: {},
      formAddAdmin: '',
      formAddUser: '',
      formAddExtensionsManifest: '',
      formAllowedIPs: '',
      formBlockedIPs: '',
      nostrAcceptedUrl: '',
      formAddIncludePath: '',
      formAddExcludePath: '',
      formAddIncludeResponseCode: '',
      isSuperUser: false,
      wallet: {},
      cancel: {},
      colors: [
        'primary',
        'secondary',
        'accent',
        'positive',
        'negative',
        'info',
        'warning',
        'red',
        'yellow',
        'orange'
      ],
      tab: 'funding',
      needsRestart: false
    }
  },
  created() {
    this.getSettings()
    this.getAudit()
    this.balance = +'{{ balance|safe }}'
  },
  computed: {
    lnbitsVersion() {
      return LNBITS_VERSION
    },
    checkChanges() {
      return !_.isEqual(this.settings, this.formData)
    },
    updateAvailable() {
      return LNBITS_VERSION !== this.statusData.version
    }
  },
  methods: {
    addAdminUser() {
      let addUser = this.formAddAdmin
      let admin_users = this.formData.lnbits_admin_users
      if (addUser && addUser.length && !admin_users.includes(addUser)) {
        this.formData.lnbits_admin_users = [...admin_users, addUser]
        this.formAddAdmin = ''
      }
    },
    removeAdminUser(user) {
      let admin_users = this.formData.lnbits_admin_users
      this.formData.lnbits_admin_users = admin_users.filter(u => u !== user)
    },
    addAllowedUser() {
      let addUser = this.formAddUser
      let allowed_users = this.formData.lnbits_allowed_users
      if (addUser && addUser.length && !allowed_users.includes(addUser)) {
        this.formData.lnbits_allowed_users = [...allowed_users, addUser]
        this.formAddUser = ''
      }
    },
    removeAllowedUser(user) {
      let allowed_users = this.formData.lnbits_allowed_users
      this.formData.lnbits_allowed_users = allowed_users.filter(u => u !== user)
    },
    addIncludePath() {
      if (!this.formAddIncludePath) {
        return
      }
      const paths = this.formData.lnbits_audit_include_paths
      if (!paths.includes(this.formAddIncludePath)) {
        this.formData.lnbits_audit_include_paths = [
          ...paths,
          this.formAddIncludePath
        ]
      }
      this.formAddIncludePath = ''
    },
    removeIncludePath(path) {
      this.formData.lnbits_audit_include_paths =
        this.formData.lnbits_audit_include_paths.filter(p => p !== path)
    },
    addExcludePath() {
      if (!this.formAddExcludePath) {
        return
      }
      const paths = this.formData.lnbits_audit_exclude_paths
      if (!paths.includes(this.formAddExcludePath)) {
        this.formData.lnbits_audit_exclude_paths = [
          ...paths,
          this.formAddExcludePath
        ]
      }
      this.formAddExcludePath = ''
    },

    removeExcludePath(path) {
      this.formData.lnbits_audit_exclude_paths =
        this.formData.lnbits_audit_exclude_paths.filter(p => p !== path)
    },
    addIncludeResponseCode() {
      if (!this.formAddIncludeResponseCode) {
        return
      }
      const codes = this.formData.lnbits_audit_http_response_codes
      if (!codes.includes(this.formAddIncludeResponseCode)) {
        this.formData.lnbits_audit_http_response_codes = [
          ...codes,
          this.formAddIncludeResponseCode
        ]
      }
      this.formAddIncludeResponseCode = ''
    },
    removeIncludeResponseCode(code) {
      this.formData.lnbits_audit_http_response_codes =
        this.formData.lnbits_audit_http_response_codes.filter(c => c !== code)
    },
    addExtensionsManifest() {
      const addManifest = this.formAddExtensionsManifest.trim()
      const manifests = this.formData.lnbits_extensions_manifests
      if (
        addManifest &&
        addManifest.length &&
        !manifests.includes(addManifest)
      ) {
        this.formData.lnbits_extensions_manifests = [...manifests, addManifest]
        this.formAddExtensionsManifest = ''
      }
    },
    removeExtensionsManifest(manifest) {
      const manifests = this.formData.lnbits_extensions_manifests
      this.formData.lnbits_extensions_manifests = manifests.filter(
        m => m !== manifest
      )
    },
    async toggleServerLog() {
      this.serverlogEnabled = !this.serverlogEnabled
      if (this.serverlogEnabled) {
        const wsProto = location.protocol !== 'http:' ? 'wss://' : 'ws://'
        const digestHex = await LNbits.utils.digestMessage(this.g.user.id)
        const localUrl =
          wsProto +
          document.domain +
          ':' +
          location.port +
          '/api/v1/ws/' +
          digestHex
        this.ws = new WebSocket(localUrl)
        this.ws.addEventListener('message', async ({data}) => {
          this.logs.push(data.toString())
          const scrollArea = this.$refs.logScroll
          if (scrollArea) {
            const scrollTarget = scrollArea.getScrollTarget()
            const duration = 0
            scrollArea.setScrollPosition(scrollTarget.scrollHeight, duration)
          }
        })
      } else {
        this.ws.close()
      }
    },
    addAllowedIPs() {
      const allowedIPs = this.formAllowedIPs.trim()
      const allowed_ips = this.formData.lnbits_allowed_ips
      if (
        allowedIPs &&
        allowedIPs.length &&
        !allowed_ips.includes(allowedIPs)
      ) {
        this.formData.lnbits_allowed_ips = [...allowed_ips, allowedIPs]
        this.formAllowedIPs = ''
      }
    },
    removeAllowedIPs(allowed_ip) {
      const allowed_ips = this.formData.lnbits_allowed_ips
      this.formData.lnbits_allowed_ips = allowed_ips.filter(
        a => a !== allowed_ip
      )
    },
    addBlockedIPs() {
      const blockedIPs = this.formBlockedIPs.trim()
      const blocked_ips = this.formData.lnbits_blocked_ips
      if (
        blockedIPs &&
        blockedIPs.length &&
        !blocked_ips.includes(blockedIPs)
      ) {
        this.formData.lnbits_blocked_ips = [...blocked_ips, blockedIPs]
        this.formBlockedIPs = ''
      }
    },
    removeBlockedIPs(blocked_ip) {
      const blocked_ips = this.formData.lnbits_blocked_ips
      this.formData.lnbits_blocked_ips = blocked_ips.filter(
        b => b !== blocked_ip
      )
    },
    addNostrUrl() {
      const url = this.nostrAcceptedUrl.trim()
      this.removeNostrUrl(url)
      this.formData.nostr_absolute_request_urls.push(url)
      this.nostrAcceptedUrl = ''
    },
    removeNostrUrl(url) {
      this.formData.nostr_absolute_request_urls =
        this.formData.nostr_absolute_request_urls.filter(b => b !== url)
    },
    restartServer() {
      LNbits.api
        .request('GET', '/admin/api/v1/restart/')
        .then(response => {
          Quasar.Notify.create({
            type: 'positive',
            message: 'Success! Restarted Server',
            icon: null
          })
          this.needsRestart = false
        })
        .catch(function (error) {
          LNbits.utils.notifyApiError(error)
        })
    },
    formatDate(date) {
      return moment(date * 1000).fromNow()
    },
    getNotifications() {
      if (this.settings.lnbits_notifications) {
        axios
          .get(this.settings.lnbits_status_manifest)
          .then(response => {
            this.statusData = response.data
          })
          .catch(error => {
            this.formData.lnbits_notifications = false
            error.response.data = {}
            error.response.data.message = 'Could not fetch status manifest.'
            LNbits.utils.notifyApiError(error)
          })
      }
    },
    getAudit() {
      LNbits.api
        .request('GET', '/admin/api/v1/audit', this.g.user.wallets[0].adminkey)
        .then(response => {
          this.auditData = response.data
        })
        .catch(function (error) {
          LNbits.utils.notifyApiError(error)
        })
    },
    getSettings() {
      LNbits.api
        .request(
          'GET',
          '/admin/api/v1/settings',
          this.g.user.wallets[0].adminkey
        )
        .then(response => {
          this.isSuperUser = response.data.is_super_user || false
          this.settings = response.data
          this.formData = {...this.settings}
          this.getNotifications()
        })
        .catch(function (error) {
          LNbits.utils.notifyApiError(error)
        })
    },
    updateSettings() {
      let data = _.omit(this.formData, [
        'is_super_user',
        'lnbits_allowed_funding_sources'
      ])
      LNbits.api
        .request(
          'PUT',
          '/admin/api/v1/settings',
          this.g.user.wallets[0].adminkey,
          data
        )
        .then(response => {
          this.needsRestart =
            this.settings.lnbits_backend_wallet_class !==
              this.formData.lnbits_backend_wallet_class ||
            this.settings.lnbits_killswitch !== this.formData.lnbits_killswitch
          this.settings = this.formData
          this.formData = _.clone(this.settings)
          Quasar.Notify.create({
            type: 'positive',
            message: `Success! Settings changed! ${
              this.needsRestart ? 'Restart required!' : ''
            }`,
            icon: null
          })
        })
        .catch(function (error) {
          LNbits.utils.notifyApiError(error)
        })
    },
    deleteSettings() {
      LNbits.utils
        .confirmDialog('Are you sure you want to restore settings to default?')
        .onOk(() => {
          LNbits.api
            .request('DELETE', '/admin/api/v1/settings')
            .then(response => {
              Quasar.Notify.create({
                type: 'positive',
                message:
                  'Success! Restored settings to defaults, restart required!',
                icon: null
              })
              this.needsRestart = true
            })
            .catch(function (error) {
              LNbits.utils.notifyApiError(error)
            })
        })
    },
    downloadBackup() {
      window.open('/admin/api/v1/backup', '_blank')
    }
  }
})
