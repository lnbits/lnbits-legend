window.app = Vue.createApp({
  el: '#vue',
  mixins: [window.windowMixin],
  data: function () {
    return {
      isSuperUser: false,
      activeWallet: {},
      wallet: {},
      cancel: {},
      users: [],
      wallets: [],
      paymentDialog: {
        show: false
      },
      walletDialog: {
        show: false
      },
      topupDialog: {
        show: false
      },
      createUserDialog: {
        data: {},
        fields: [
          {
            description: 'Username',
            name: 'username'
          },
          {
            description: 'Email',
            name: 'email'
          },
          {
            type: 'password',
            description: 'Password',
            name: 'password'
          }
        ],
        show: false
      },
      createWalletDialog: {
        data: {},
        fields: [
          {
            type: 'str',
            description: 'Wallet Name',
            name: 'name'
          },
          {
            type: 'select',
            values: ['', 'EUR', 'USD'],
            description: 'Currency',
            name: 'currency'
          },
          {
            type: 'str',
            description: 'Balance',
            name: 'balance'
          }
        ],
        show: false
      },
      walletTable: {
        columns: [
          {
            name: 'name',
            align: 'left',
            label: 'Name',
            field: 'name'
          },
          {
            name: 'currency',
            align: 'left',
            label: 'Currency',
            field: 'currency'
          },
          {
            name: 'balance_msat',
            align: 'left',
            label: 'Balance',
            field: 'balance_msat'
          },
          {
            name: 'deleted',
            align: 'left',
            label: 'Deleted',
            field: 'deleted'
          }
        ]
      },
      usersTable: {
        columns: [
          {
            name: 'balance_msat',
            align: 'left',
            label: 'Balance',
            field: 'balance_msat',
            sortable: true
          },
          {
            name: 'wallet_count',
            align: 'left',
            label: 'Wallet Count',
            field: 'wallet_count',
            sortable: true
          },
          {
            name: 'transaction_count',
            align: 'left',
            label: 'Transaction Count',
            field: 'transaction_count',
            sortable: true
          },
          {
            name: 'username',
            align: 'left',
            label: 'Username',
            field: 'username',
            sortable: true
          },
          {
            name: 'email',
            align: 'left',
            label: 'Email',
            field: 'email',
            sortable: true
          },
          {
            name: 'last_payment',
            align: 'left',
            label: 'Last Payment',
            field: 'last_payment',
            sortable: true
          }
        ],
        pagination: {
          sortBy: 'balance_msat',
          rowsPerPage: 10,
          page: 1,
          descending: true,
          rowsNumber: 10
        },
        search: null,
        hideEmpty: true,
        loading: false
      }
    }
  },
  watch: {
    'usersTable.hideEmpty': function (newVal, _) {
      if (newVal) {
        this.usersTable.filter = {
          'transaction_count[gt]': 0
        }
      } else {
        this.usersTable.filter = {}
      }
      this.fetchUsers()
    }
  },
  created() {
    this.fetchUsers()
  },
  mounted() {
    this.chart1 = new Chart(this.$refs.chart1.getContext('2d'), {
      type: 'bubble',
      options: {
        scales: {
          x: {
            type: 'linear',
            beginAtZero: true,
            title: {
              text: 'Transaction count'
            }
          },
          y: {
            type: 'linear',
            beginAtZero: true,
            title: {
              text: 'User balance in million sats'
            }
          }
        },
        tooltip: {
          callbacks: {
            label: function (tooltipItem, data) {
              const dataset = data.datasets[tooltipItem.datasetIndex]
              const dataPoint = dataset.data[tooltipItem.index]
              return dataPoint.customLabel || ''
            }
          }
        },
        layout: {
          padding: 10
        }
      },
      data: {
        datasets: [
          {
            label: 'Wallet balance vs transaction count',
            backgroundColor: 'rgb(255, 99, 132)',
            data: []
          }
        ]
      }
    })
  },
  methods: {
    formatDate: function (value) {
      return LNbits.utils.formatDate(value)
    },
    formatSat: function (value) {
      return LNbits.utils.formatSat(Math.floor(value / 1000))
    },
    resetPassword(user_id) {
      return LNbits.api
        .request('PUT', `/users/api/v1/user/${user_id}/reset_password`)
        .then(res => {
          this.$q.notify({
            type: 'positive',
            message: 'generated key for password reset',
            icon: null
          })
          const url = window.location.origin + '?reset_key=' + res.data
          this.copyText(url)
        })
        .catch(function (error) {
          LNbits.utils.notifyApiError(error)
        })
    },
    createUser() {
      LNbits.api
        .request('POST', '/users/api/v1/user', null, this.createUserDialog.data)
        .then(() => {
          this.fetchUsers()
          Quasar.Notify.create({
            type: 'positive',
            message: 'Success! User created!',
            icon: null
          })
        })
        .catch(function (error) {
          LNbits.utils.notifyApiError(error)
        })
    },
    createWallet(user_id) {
      LNbits.api
        .request(
          'POST',
          `/users/api/v1/user/${user_id}/wallet`,
          null,
          this.createWalletDialog.data
        )
        .then(() => {
          this.fetchUsers()
          Quasar.Notify.create({
            type: 'positive',
            message: 'Success! User created!',
            icon: null
          })
        })
        .catch(function (error) {
          LNbits.utils.notifyApiError(error)
        })
    },
    deleteUser(user_id) {
      LNbits.utils
        .confirmDialog('Are you sure you want to delete this user?')
        .onOk(() => {
          LNbits.api
            .request('DELETE', `/users/api/v1/user/${user_id}`)
            .then(() => {
              this.fetchUsers()
              Quasar.Notify.create({
                type: 'positive',
                message: 'Success! User deleted!',
                icon: null
              })
            })
            .catch(function (error) {
              LNbits.utils.notifyApiError(error)
            })
        })
    },
    undeleteUserWallet(user_id, wallet) {
      LNbits.api
        .request(
          'GET',
          `/users/api/v1/user/${user_id}/wallet/${wallet}/undelete`
        )
        .then(() => {
          this.fetchWallets(user_id)
          Quasar.Notify.create({
            type: 'positive',
            message: 'Success! Undeleted user wallet!',
            icon: null
          })
        })
        .catch(function (error) {
          LNbits.utils.notifyApiError(error)
        })
    },
    deleteUserWallet(user_id, wallet, deleted) {
      const dialogText = deleted
        ? 'Wallet is already deleted, are you sure you want to permanently delete this user wallet?'
        : 'Are you sure you want to delete this user wallet?'
      LNbits.utils.confirmDialog(dialogText).onOk(() => {
        LNbits.api
          .request('DELETE', `/users/api/v1/user/${user_id}/wallet/${wallet}`)
          .then(() => {
            this.fetchWallets(user_id)
            Quasar.Notify.create({
              type: 'positive',
              message: 'Success! User wallet deleted!',
              icon: null
            })
          })
          .catch(function (error) {
            LNbits.utils.notifyApiError(error)
          })
      })
    },
    updateChart(users) {
      const filtered = users.filter(user => {
        if (
          user.balance_msat === null ||
          user.balance_msat === 0 ||
          user.wallet_count === 0
        ) {
          return false
        }
        return true
      })

      const data = filtered.map(user => {
        const labelUsername = `${user.username ? 'User: ' + user.username + '. ' : ''}`
        const userBalanceSats = Math.floor(
          user.balance_msat / 1000
        ).toLocaleString()
        return {
          x: user.transaction_count,
          y: user.balance_msat / 1000000000,
          r: 4,
          customLabel:
            labelUsername +
            'Balance: ' +
            userBalanceSats +
            ' sats. Tx count: ' +
            user.transaction_count
        }
      })
      this.chart1.data.datasets[0].data = data
      this.chart1.update()
    },
    fetchUsers(props) {
      const params = LNbits.utils.prepareFilterQuery(this.usersTable, props)
      LNbits.api
        .request('GET', `/users/api/v1/user?${params}`)
        .then(res => {
          this.usersTable.loading = false
          this.usersTable.pagination.rowsNumber = res.data.total
          this.users = res.data.data
          this.updateChart(this.users)
        })
        .catch(function (error) {
          LNbits.utils.notifyApiError(error)
        })
    },
    fetchWallets(user_id) {
      LNbits.api
        .request('GET', `/users/api/v1/user/${user_id}/wallet`)
        .then(res => {
          this.wallets = res.data
          this.walletDialog.show = this.wallets.length > 0
          if (!this.walletDialog.show) {
            this.fetchUsers()
          }
        })
        .catch(function (error) {
          LNbits.utils.notifyApiError(error)
        })
    },
    showPayments(wallet_id) {
      this.activeWallet = this.wallets.find(wallet => wallet.id === wallet_id)
      this.paymentDialog.show = true
    },
    toggleAdmin(user_id) {
      LNbits.api
        .request('GET', `/users/api/v1/user/${user_id}/admin`)
        .then(() => {
          this.fetchUsers()
          Quasar.Notify.create({
            type: 'positive',
            message: 'Success! Toggled admin!',
            icon: null
          })
        })
        .catch(function (error) {
          LNbits.utils.notifyApiError(error)
        })
    },
    exportUsers() {
      console.log('export users')
    },
    topupCallback(res) {
      if (res.success) {
        this.wallets.forEach(wallet => {
          if (res.wallet_id === wallet.id) {
            wallet.balance_msat += res.credit * 1000
          }
        })
        this.fetchUsers()
      }
    },
    topupWallet() {
      LNbits.api
        .request(
          'PUT',
          '/users/api/v1/topup',
          this.g.user.wallets[0].adminkey,
          this.wallet
        )
        .then(_ => {
          Quasar.Notify.create({
            type: 'positive',
            message: `Success! Added ${this.wallet.amount} to ${this.wallet.id}`,
            icon: null
          })
          this.wallet = {}
        })
        .catch(function (error) {
          LNbits.utils.notifyApiError(error)
        })
    }
  }
})
