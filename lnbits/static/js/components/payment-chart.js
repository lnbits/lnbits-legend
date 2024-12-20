function generateChart(canvas, rawData) {
  const data = rawData.reduce(
    (previous, current) => {
      previous.labels.push(current.date)
      previous.income.push(current.income)
      previous.spending.push(current.spending)
      previous.cumulative.push(current.balance)
      return previous
    },
    {
      labels: [],
      income: [],
      spending: [],
      cumulative: []
    }
  )

  return new Chart(canvas.getContext('2d'), {
    type: 'bar',
    data: {
      labels: data.labels,
      datasets: [
        {
          data: data.cumulative,
          type: 'line',
          label: 'balance',
          backgroundColor: '#673ab7', // deep-purple
          borderColor: '#673ab7',
          borderWidth: 4,
          pointRadius: 3,
          fill: false
        },
        {
          data: data.income,
          type: 'bar',
          label: 'in',
          barPercentage: 0.75,
          backgroundColor: 'rgba(76, 175, 80, 0.5)' // green
        },
        {
          data: data.spending,
          type: 'bar',
          label: 'out',
          barPercentage: 0.75,
          backgroundColor: 'rgba(233, 30, 99, 0.5)' // pink
        }
      ]
    },
    options: {
      title: {
        text: 'Chart.js Combo Time Scale'
      },
      tooltips: {
        mode: 'index',
        intersect: false
      },
      scales: {
        xAxes: [
          {
            type: 'time',
            display: true,
            //offset: true,
            time: {
              minUnit: 'hour',
              stepSize: 3
            }
          }
        ]
      },
      // performance tweaks
      animation: {
        duration: 0
      },
      elements: {
        line: {
          tension: 0
        }
      }
    }
  })
}

window.app.component('payment-chart', {
  template: '#payment-chart',
  name: 'payment-chart',
  props: ['wallet'],
  mixins: [window.windowMixin],
  data() {
    return {
      paymentsChart: {
        show: false,
        group: {
          value: 'hour',
          label: 'Hour'
        },
        groupOptions: [
          {value: 'hour', label: 'Hour'},
          {value: 'day', label: 'Day'},
          {value: 'week', label: 'Week'},
          {value: 'month', label: 'Month'},
          {value: 'year', label: 'Year'}
        ],
        instance: null
      }
    }
  },
  methods: {
    showChart() {
      this.paymentsChart.show = true
      LNbits.api
        .request(
          'GET',
          '/api/v1/payments/history?group=' + this.paymentsChart.group.value,
          this.wallet.adminkey
        )
        .then(response => {
          this.$nextTick(() => {
            if (this.paymentsChart.instance) {
              this.paymentsChart.instance.destroy()
            }
            this.paymentsChart.instance = generateChart(
              this.$refs.canvas,
              response.data
            )
          })
        })
        .catch(err => {
          LNbits.utils.notifyApiError(err)
          this.paymentsChart.show = false
        })
    }
  }
})

window.app.component('wallet-payment-chart', {
  template: '#wallet-payment-chart',
  name: 'wallet-payment-chart',
  props: ['wallet'],
  mixins: [window.windowMixin],
  data() {
    return {
      chartData: [],
      primaryColor: this.$q.localStorage.getItem('lnbits.primaryColor')
    }
  },
  mounted() {
    LNbits.api
      .request(
        'GET',
        '/api/v1/payments/history?group=hour',
        this.wallet.adminkey
      )
      .then(response => {
        this.chartData = response.data
        this.$nextTick(() => {
          this.transactionChart = new Chart(
            this.$refs.transactionChart.getContext('2d'),
            {
              type: 'line',
              options: {
                responsive: true,
                maintainAspectRatio: false,
                layout: {
                  padding: 0,
                  margin: 0
                },
                plugins: {
                  legend: {
                    display: false
                  },
                  title: {
                    display: false
                  },
                  tooltip: {
                    enabled: true, // Enable tooltips to show data on hover
                    callbacks: {
                      // Custom tooltip callback to show amount and time
                      label: tooltipItem => {
                        const index = tooltipItem.dataIndex
                        const transaction = this.chartData[index] // Match tooltip point with transaction
                        const amount = transaction.balance
                        return [
                          `Balance: ${tooltipItem.raw / 1000}sats`, // Display cumulative balance
                          `Amount: ${amount / 1000}sats`
                        ]
                      }
                    }
                  }
                },
                elements: {
                  point: {
                    radius: 4, // Points will now be visible
                    hoverRadius: 6 // Increase point size on hover
                  }
                },
                scales: {
                  x: {
                    display: false
                  },
                  y: {
                    display: false
                  }
                }
              },
              data: {
                labels: this.chartData.map(
                  tx => new Date(tx.date).toLocaleString() // Use time for labels
                ),
                datasets: [
                  {
                    label: 'Cumulative Balance',
                    data: this.chartData.map(tx => tx.balance / 1000), // Display cumulative balance
                    backgroundColor: LNbits.utils.hexAlpha(
                      this.primaryColor,
                      0.3
                    ),
                    borderColor: this.primaryColor,
                    borderWidth: 2,
                    fill: true,
                    tension: 0.2
                  }
                ]
              }
            }
          )
        })
      })
      .catch(LNbits.utils.notifyApiError)
  }
})
