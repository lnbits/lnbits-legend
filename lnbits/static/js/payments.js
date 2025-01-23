window.PaymentsPageLogic = {
  mixins: [window.windowMixin],
  data() {
    return {
      payments: [],
      searchData: {
        wallet_id: null,
        payment_hash: null,
        status: null,
        memo: null,
        tag: null
      },
      searchOptions: {
        status: [],
        tag: []
      },
      paymentsTable: {
        columns: [
          {
            name: 'wallet_id',
            align: 'left',
            label: 'Wallet (ID)',
            field: 'wallet_id',
            sortable: false
          },
          {
            name: 'amount',
            align: 'left',
            label: 'Amount',
            field: 'amount',
            sortable: true
          },
          {
            name: 'fee',
            align: 'left',
            label: 'Fee',
            field: 'fee',
            sortable: true
          },
          {
            name: 'memo',
            align: 'left',
            label: 'Memo',
            field: 'memo',
            sortable: false
          },
          {
            name: 'status',
            align: 'left',
            label: 'Status',
            field: 'status',
            sortable: false
          },
          {
            name: 'tag',
            align: 'left',
            label: 'Ext. Tag',
            field: 'tag',
            sortable: false
          },
          {
            name: 'payment_hash',
            align: 'left',
            label: 'Payment Hash',
            field: 'payment_hash',
            sortable: false
          },
          {
            name: 'created_at',
            align: 'left',
            label: 'Created At',
            field: 'created_at',
            sortable: true
          }
        ],
        pagination: {
          sortBy: 'created_at',
          rowsPerPage: 10,
          page: 1,
          descending: true,
          rowsNumber: 10
        },
        search: null,
        hideEmpty: true,
        loading: true
      },
      chartsReady: false,
      showDetails: false,
      paymentDetails: null
      // showInternal: false
    }
  },
  async mounted() {
    this.chartsReady = true
    await this.$nextTick()
    this.initCharts()
    await this.fetchPayments()
  },
  computed: {},
  methods: {
    async fetchPayments(props) {
      console.log('fetchPayments')
      try {
        const params = LNbits.utils.prepareFilterQuery(
          this.paymentsTable,
          props
        )
        const {data} = await LNbits.api.request(
          'GET',
          `/api/v1/payments/all/paginated?${params}`
        )

        this.paymentsTable.pagination.rowsNumber = data.total
        this.payments = data.data.map(p => {
          if (p.extra && p.extra.tag) {
            p.tag = p.extra.tag
          }
          return p
        })
        this.searchOptions.status = Array.from(
          new Set(data.data.map(p => p.status))
        )
        this.searchOptions.tag = Array.from(
          new Set(data.data.map(p => p.extra && p.extra.tag))
        )
      } catch (error) {
        console.error(error)
        LNbits.utils.notifyApiError(error)
      } finally {
        this.paymentsTable.loading = false
        this.updateCharts()
      }
    },
    async searchPaymentsBy(fieldName, fieldValue) {
      if (fieldName) {
        this.searchData[fieldName] = fieldValue
      }
      // remove empty fields
      this.paymentsTable.filter = Object.entries(this.searchData).reduce(
        (a, [k, v]) => (v ? ((a[k] = v), a) : a),
        {}
      )

      await this.fetchPayments()
    },
    showDetailsToggle(payment) {
      this.paymentDetails = payment
      return (this.showDetails = !this.showDetails)
    },
    formatDate(dateString) {
      return LNbits.utils.formatDateString(dateString)
    },
    shortify(value) {
      valueLength = (value || '').length
      if (valueLength <= 10) {
        return value
      }
      return `${value.substring(0, 5)}...${value.substring(valueLength - 5, valueLength)}`
    },
    updateCharts() {
      if (this.payments.length === 0) {
        return
      }

      const payIn = this.payments.filter(p => p.amount > 0)
      const payOut = this.payments.filter(p => p.amount < 0)
      const paymentsStatus = this.payments.reduce((acc, p) => {
        acc[p.status] = (acc[p.status] || 0) + 1
        return acc
      }, {})

      this.paymentsStatusChart.data.datasets = [
        {
          label: 'Status',
          data: Object.values(paymentsStatus)
        }
      ]
      this.paymentsStatusChart.data.labels = Object.keys(paymentsStatus)
      this.paymentsStatusChart.update()

      const dates = this.payments.reduce((acc, p) => {
        const date = Quasar.date.formatDate(new Date(p.created_at), 'MM/YYYY')
        if (!acc.includes(date)) {
          acc.push(date)
        }
        return acc
      }, [])

      const incomingData = dates.map(date => {
        return payIn
          .filter(
            p =>
              Quasar.date.formatDate(new Date(p.created_at), 'MM/YYYY') === date
          )
          .reduce((sum, p) => sum + p.amount, 0)
      })

      const outgoingData = dates.map(date => {
        return payOut
          .filter(
            p =>
              Quasar.date.formatDate(new Date(p.created_at), 'MM/YYYY') === date
          )
          .reduce((sum, p) => sum + p.amount, 0)
      })

      this.paymentsFlowChart.data.labels = dates
      this.paymentsFlowChart.data.datasets = [
        {
          label: 'Incoming',
          data: incomingData,
          backgroundColor: 'rgb(54, 162, 235)'
        },
        {
          label: 'Outgoing',
          data: outgoingData,
          backgroundColor: 'rgb(255, 99, 132)'
        }
      ]
      this.paymentsFlowChart.update()

      const wallets = this.payments.reduce((acc, p) => {
        if (!acc[p.wallet_id]) {
          acc[p.wallet_id] = {count: 0, balance: 0}
        }
        acc[p.wallet_id].count += 1
        acc[p.wallet_id].balance += p.amount
        return acc
      }, {})

      const balances = Object.values(wallets).map(w => w.balance)

      const min = Math.min(...balances)
      const max = Math.max(...balances)

      const scale = balance => {
        return 3 + ((balance - min) * (30 - 3)) / (max - min)
      }

      const walletsData = Object.entries(wallets).map(
        ([wallet_id, {count, balance}]) => {
          return {
            x: count,
            y: balance,
            r: scale(balance)
          }
        }
      )

      this.paymentsWalletsChart.data.datasets = [
        {
          label: 'Wallets',
          data: walletsData,
          backgroundColor: walletsData.map(
            () => `hsl(${Math.random() * 360}, 100%, 50%)`
          )
        }
      ]
      this.paymentsWalletsChart.update()
    },
    async initCharts() {
      if (!this.chartsReady) {
        console.warn('Charts are not ready yet. Initialization delayed.')
        return
      }
      this.paymentsFlowChart = new Chart(
        this.$refs.paymentsFlowChart.getContext('2d'),
        {
          type: 'bar',

          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              title: {
                display: false
              }
            },
            scales: {
              x: {
                stacked: true
              },
              y: {
                stacked: true
              }
            }
          },
          data: {
            datasets: [
              {
                label: '',
                data: [],
                backgroundColor: [
                  'rgb(255, 99, 132)',
                  'rgb(54, 162, 235)',
                  'rgb(255, 205, 86)',
                  'rgb(255, 5, 86)',
                  'rgb(25, 205, 86)',
                  'rgb(255, 205, 250)'
                ],
                hoverOffset: 4
              }
            ]
          }
        }
      )
      this.paymentsStatusChart = new Chart(
        this.$refs.paymentsStatusChart.getContext('2d'),
        {
          type: 'doughnut',

          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              title: {
                display: false
              }
            }
          },
          data: {
            datasets: [
              {
                label: '',
                data: [],
                backgroundColor: [
                  'rgb(255, 99, 132)',
                  'rgb(54, 162, 235)',
                  'rgb(255, 205, 86)',
                  'rgb(255, 5, 86)',
                  'rgb(25, 205, 86)',
                  'rgb(255, 205, 250)'
                ],
                hoverOffset: 4
              }
            ]
          }
        }
      )
      this.paymentsWalletsChart = new Chart(
        this.$refs.paymentsWalletsChart.getContext('2d'),
        {
          type: 'bubble',

          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              title: {
                display: false
              }
            }
          },
          data: {
            datasets: [
              {
                label: '',
                data: [],
                backgroundColor: [
                  'rgb(255, 99, 132)',
                  'rgb(54, 162, 235)',
                  'rgb(255, 205, 86)',
                  'rgb(255, 5, 86)',
                  'rgb(25, 205, 86)',
                  'rgb(255, 205, 250)'
                ],
                hoverOffset: 4
              }
            ]
          }
        }
      )
    }
  }
}
