window.app.component('lnbits-funding-sources', {
  template: '#lnbits-funding-sources',
  mixins: [window.windowMixin],
  props: ['form-data', 'allowed-funding-sources'],
  methods: {
    getFundingSourceLabel(item) {
      const fundingSource = this.rawFundingSources.find(
        fundingSource => fundingSource[0] === item
      )
      return fundingSource ? fundingSource[1] : item
    }
  },
  computed: {
    fundingSources() {
      let tmp = []
      for (const [key, _, obj] of this.rawFundingSources) {
        const tmpObj = {}
        if (obj !== null) {
          for (let [k, v] of Object.entries(obj)) {
            tmpObj[k] = {label: v, value: null}
          }
        }
        tmp.push([key, tmpObj])
      }
      return new Map(tmp)
    },
    sortedAllowedFundingSources() {
      return this.allowedFundingSources.sort()
    }
  },
  data() {
    return {
      rawFundingSources: [
        ['VoidWallet', 'Void Wallet', null],
        [
          'FakeWallet',
          'Fake Wallet',
          {
            fake_wallet_secret: 'Secret'
          }
        ],
        [
          'CoreLightningWallet',
          'Core Lightning',
          {
            corelightning_rpc: 'Endpoint',
            corelightning_pay_command: 'Custom Pay Command'
          }
        ],
        [
          'CoreLightningRestWallet',
          'Core Lightning Rest',
          {
            corelightning_rest_url: 'Endpoint',
            corelightning_rest_cert: 'Certificate',
            corelightning_rest_macaroon: 'Macaroon'
          }
        ],
        [
          'LndRestWallet',
          'Lightning Network Daemon (LND Rest)',
          {
            lnd_rest_endpoint: 'Endpoint',
            lnd_rest_cert: 'Certificate',
            lnd_rest_macaroon: 'Macaroon',
            lnd_rest_macaroon_encrypted: 'Encrypted Macaroon',
            lnd_rest_route_hints: 'Enable Route Hints',
            lnd_rest_allow_self_payment: 'Allow Self Payment'
          }
        ],
        [
          'LndWallet',
          'Lightning Network Daemon (LND)',
          {
            lnd_grpc_endpoint: 'Endpoint',
            lnd_grpc_cert: 'Certificate',
            lnd_grpc_port: 'Port',
            lnd_grpc_admin_macaroon: 'Admin Macaroon',
            lnd_grpc_macaroon_encrypted: 'Encrypted Macaroon'
          }
        ],
        [
          'LnTipsWallet',
          'LN.Tips',
          {
            lntips_api_endpoint: 'Endpoint',
            lntips_api_key: 'API Key'
          }
        ],
        [
          'LNPayWallet',
          'LN Pay',
          {
            lnpay_api_endpoint: 'Endpoint',
            lnpay_api_key: 'API Key',
            lnpay_wallet_key: 'Wallet Key'
          }
        ],
        [
          'EclairWallet',
          'Eclair (ACINQ)',
          {
            eclair_url: 'URL',
            eclair_pass: 'Password'
          }
        ],
        [
          'LNbitsWallet',
          'LNbits',
          {
            lnbits_endpoint: 'Endpoint',
            lnbits_key: 'Admin Key'
          }
        ],
        [
          'BlinkWallet',
          'Blink',
          {
            blink_api_endpoint: 'Endpoint',
            blink_ws_endpoint: 'WebSocket',
            blink_token: 'Key'
          }
        ],
        [
          'AlbyWallet',
          'Alby',
          {
            alby_api_endpoint: 'Endpoint',
            alby_access_token: 'Key'
          }
        ],
        [
          'BoltzWallet',
          'Boltz',
          {
            boltz_client_endpoint: 'Endpoint',
            boltz_client_macaroon: 'Admin Macaroon path or hex',
            boltz_client_cert: 'Certificate path or hex',
            boltz_client_wallet: 'Wallet Name'
          }
        ],
        [
          'ZBDWallet',
          'ZBD',
          {
            zbd_api_endpoint: 'Endpoint',
            zbd_api_key: 'Key'
          }
        ],
        [
          'PhoenixdWallet',
          'Phoenixd',
          {
            phoenixd_api_endpoint: 'Endpoint',
            phoenixd_api_password: 'Key'
          }
        ],
        [
          'OpenNodeWallet',
          'OpenNode',
          {
            opennode_api_endpoint: 'Endpoint',
            opennode_key: 'Key'
          }
        ],
        [
          'ClicheWallet',
          'Cliche (NBD)',
          {
            cliche_endpoint: 'Endpoint'
          }
        ],
        [
          'SparkWallet',
          'Spark',
          {
            spark_url: 'Endpoint',
            spark_token: 'Token'
          }
        ],
        [
          'NWCWallet',
          'Nostr Wallet Connect',
          {
            nwc_pairing_url: 'Pairing URL'
          }
        ],
        [
          'BreezSdkWallet',
          'Breez SDK',
          {
            breez_api_key: 'Breez API Key',
            breez_greenlight_seed: 'Greenlight Seed',
            breez_greenlight_device_key: 'Greenlight Device Key',
            breez_greenlight_device_cert: 'Greenlight Device Cert',
            breez_greenlight_invite_code: 'Greenlight Invite Code'
          }
        ],
        [
          'BreezLiquidSdkWallet',
          'Breez Liquid SDK',
          {
            breez_liquid_api_key: 'Breez API Key',
            breez_liquid_seed: 'Liquid seed phrase'
          }
        ]
      ]
    }
  }
})
