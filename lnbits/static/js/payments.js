const { methods } = require("underscore")

window.PaymentsPageLogic = {
  mixins: [window.windowMixins],
  data() {
    return {
      payments: [],
    }
  },
  async mounted() {
    this.payments = await this.fetchPayments()
  },
  methods: {
    async fetchPayments() {
      try {
        const { data } = await LNbits.api.request(
          "GET",
          `/api/v1/payments?wallet=${this.wallet.id}`
        )
      }
    }
  }
}
