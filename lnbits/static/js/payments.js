window.PaymentsPageLogic = {
  mixins: [window.windowMixin],
  data() {
    return {
      payments: [],
      searchData: {
        wallet_id: null,
        payment_hash: null,
        status: null,
        memo: null
        //tag: null // not used, payments don't have tag, only the extra
      },
      searchOptions: {
        status: []
        // tag: [] // not used, payments don't have tag, only the extra
      },
      paymentsTable: {
        columns: [
          {
            name: 'status',
            align: 'left',
            label: 'Status',
            field: 'status',
            sortable: false
          },
          {
            name: 'created_at',
            align: 'left',
            label: 'Created At',
            field: 'created_at',
            sortable: true
          },

          {
            name: 'amountFormatted',
            align: 'left',
            label: 'Amount',
            field: 'amountFormatted',
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
            name: 'tag',
            align: 'left',
            label: 'Ext. Tag',
            field: 'tag',
            sortable: false
          },
          {
            name: 'memo',
            align: 'left',
            label: 'Memo',
            field: 'memo',
            sortable: false
          },
          {
            name: 'wallet_id',
            align: 'left',
            label: 'Wallet (ID)',
            field: 'wallet_id',
            sortable: false
          },

          {
            name: 'payment_hash',
            align: 'left',
            label: 'Payment Hash',
            field: 'payment_hash',
            sortable: false
          }
        ],
        pagination: {
          sortBy: 'created_at',
          rowsPerPage: 25,
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
          p.timeFrom = moment(p.created_at).fromNow()

          p.amountFormatted = new Intl.NumberFormat(window.LOCALE).format(
            p.amount / 1000
          )
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

      const wallets = this.payments.reduce((acc, p) => {
        if (!acc[p.wallet_id]) {
          acc[p.wallet_id] = {count: 0, balance: 0}
        }
        acc[p.wallet_id].count += 1
        acc[p.wallet_id].balance += p.amount
        return acc
      }, {})

      const counts = Object.values(wallets).map(w => w.count)

      const min = Math.min(...counts)
      const max = Math.max(...counts)

      const scale = val => {
        return Math.floor(3 + ((val - min) * (25 - 3)) / (max - min))
      }

      const walletsData = Object.entries(wallets).map(
        ([_, {count, balance}]) => {
          return {
            x: count,
            y: balance,
            r: scale(count)
          }
        }
      )

      this.paymentsWalletsChart.data.datasets = walletsData.map((w, i) => {
        return {
          label: Object.keys(wallets)[i],
          data: [w]
          // backgroundColor: walletsData.map(
          //   () => `hsl(${Math.random() * 360}, 100%, 50%)`
          // )
        }
      })

      this.paymentsWalletsChart.update()

      const tags = this.payments.reduce((acc, p) => {
        const tag = p.extra && p.extra.tag ? p.extra.tag : 'wallet'
        acc[tag] = (acc[tag] || 0) + 1
        return acc
      }, {})
      this.paymentsTagsChart.data.datasets = [
        {
          label: 'Tags',
          data: Object.values(tags)
        }
      ]
      this.paymentsTagsChart.data.labels = Object.keys(tags)
      this.paymentsTagsChart.update()
    },
    async initCharts() {
      if (!this.chartsReady) {
        console.warn('Charts are not ready yet. Initialization delayed.')
        return
      }
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
            },
            onClick: (_, elements, chart) => {
              if (elements[0]) {
                const i = elements[0].index
                this.searchPaymentsBy('status', chart.data.labels[i])
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
              legend: {
                display: false
              },
              title: {
                display: false
              }
            },
            onClick: (_, elements, chart) => {
              if (elements[0]) {
                const i = elements[0].datasetIndex
                this.searchPaymentsBy('wallet_id', chart.data.datasets[i].label)
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
      this.paymentsTagsChart = new Chart(
        this.$refs.paymentsTagsChart.getContext('2d'),
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
    }
  }
}