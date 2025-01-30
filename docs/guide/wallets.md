---
layout: default
title: Backend wallets
nav_order: 3
---

# Backend wallets

LNbits can run on top of many Lightning Network funding sources with more being added regularly.

A backend wallet can be configured using the following LNbits environment variables:

### CoreLightning

- `LNBITS_BACKEND_WALLET_CLASS`: **CoreLightningWallet**
- `CORELIGHTNING_RPC`: /file/path/lightning-rpc

### CoreLightning REST

- `LNBITS_BACKEND_WALLET_CLASS`: **CoreLightningRestWallet**
- `CORELIGHTNING_REST_URL`: http://127.0.0.1:8185/
- `CORELIGHTNING_REST_MACAROON`: /file/path/admin.macaroon or Base64/Hex
- `CORELIGHTNING_REST_CERT`: /home/lightning/clnrest/tls.cert

### Spark (Core Lightning)

- `LNBITS_BACKEND_WALLET_CLASS`: **SparkWallet**
- `SPARK_URL`: http://10.147.17.230:9737/rpc
- `SPARK_TOKEN`: secret_access_key

### LND (REST)

- `LNBITS_BACKEND_WALLET_CLASS`: **LndRestWallet**
- `LND_REST_ENDPOINT`: http://10.147.17.230:8080/
- `LND_REST_CERT`: /file/path/tls.cert
- `LND_REST_MACAROON`: /file/path/admin.macaroon or Base64/Hex

or

- `LND_REST_MACAROON_ENCRYPTED`: eNcRyPtEdMaCaRoOn

### LND (gRPC)

- `LNBITS_BACKEND_WALLET_CLASS`: **LndWallet**
- `LND_GRPC_ENDPOINT`: ip_address
- `LND_GRPC_PORT`: port
- `LND_GRPC_CERT`: /file/path/tls.cert
- `LND_GRPC_MACAROON`: /file/path/admin.macaroon or Base64/Hex

You can also use an AES-encrypted macaroon (more info) instead by using

- `LND_GRPC_MACAROON_ENCRYPTED`: eNcRyPtEdMaCaRoOn

To encrypt your macaroon, run `poetry run python lnbits/wallets/macaroon/macaroon.py`.

### LNbits

- `LNBITS_BACKEND_WALLET_CLASS`: **LNbitsWallet**
- `LNBITS_ENDPOINT`: e.g. https://lnbits.com
- `LNBITS_KEY`: lnbitsAdminKey

### LNPay

For the invoice listener to work you have a publicly accessible URL in your LNbits and must set up [LNPay webhooks](https://dashboard.lnpay.co/webhook/) pointing to `<your LNbits host>/wallet/webhook` with the "Wallet Receive" event and no secret. For example, `https://mylnbits/wallet/webhook` will be the Endpoint Url that gets notified about the payment.

- `LNBITS_BACKEND_WALLET_CLASS`: **LNPayWallet**
- `LNPAY_API_ENDPOINT`: https://api.lnpay.co/v1/
- `LNPAY_API_KEY`: sak_apiKey
- `LNPAY_WALLET_KEY`: waka_apiKey

### OpenNode

For the invoice to work you must have a publicly accessible URL in your LNbits. No manual webhook setting is necessary.

- `LNBITS_BACKEND_WALLET_CLASS`: **OpenNodeWallet**
- `OPENNODE_API_ENDPOINT`: https://api.opennode.com/
- `OPENNODE_KEY`: opennodeAdminApiKey

### Blink

For the invoice to work you must have a publicly accessible URL in your LNbits. No manual webhook setting is necessary. You can generate a Blink API key after logging in or creating a new Blink account at: https://dashboard.blink.sv. For more info visit: https://dev.blink.sv/api/auth#create-an-api-key```

- `LNBITS_BACKEND_WALLET_CLASS`: **BlinkWallet**
- `BLINK_API_ENDPOINT`: https://api.blink.sv/graphql
- `BLINK_WS_ENDPOINT`: wss://ws.blink.sv/graphql
- `BLINK_TOKEN`: BlinkToken

### Alby

For the invoice to work you must have a publicly accessible URL in your LNbits. No manual webhook setting is necessary. You can generate an alby access token here: https://getalby.com/developer/access_tokens/new

- `LNBITS_BACKEND_WALLET_CLASS`: **AlbyWallet**
- `ALBY_API_ENDPOINT`: https://api.getalby.com/
- `ALBY_ACCESS_TOKEN`: AlbyAccessToken

### Boltz

This funding source connects to a running [boltz-client](https://docs.boltz.exchange/v/boltz-client) and handles all lightning payments through submarine swaps on the liquid network.
You can configure the daemon to run in standalone mode by `standalone = True` in the config file or using the cli flag (`boltzd --standalone`).
Once running, you can create a liquid wallet using `boltzcli wallet create lnbits lbtc`.

- `LNBITS_BACKEND_WALLET_CLASS`: **BoltzWallet**
- `BOLTZ_CLIENT_ENDPOINT`: 127.0.0.1:9002
- `BOLTZ_CLIENT_MACAROON`: /home/bob/.boltz/macaroons/admin.macaroon or Base64/Hex
- `BOLTZ_CLIENT_CERT`: /home/bob/.boltz/tls.cert or Base64/Hex
- `BOLTZ_CLIENT_WALLET`: lnbits

### ZBD

For the invoice to work you must have a publicly accessible URL in your LNbits. No manual webhook setting is necessary. You can generate an ZBD API Key here: https://zbd.dev/docs/dashboard/projects/api

- `LNBITS_BACKEND_WALLET_CLASS`: **ZBDWallet**
- `ZBD_API_ENDPOINT`: https://api.zebedee.io/v0/
- `ZBD_API_KEY`: ZBDApiKey

### Phoenixd

For the invoice to work you must have a publicly accessible URL in your LNbits. You can get a phoenixd API key from the install
~/.phoenix/phoenix.conf, also see the documentation for phoenixd.

- `LNBITS_BACKEND_WALLET_CLASS`: **PhoenixdWallet**
- `PHOENIXD_API_ENDPOINT`: http://localhost:9740/
- `PHOENIXD_API_PASSWORD`: PhoenixdApiPassword

### Breez SDK

A Greenlight invite code or Greenlight partner certificate/key can be used to register a new node with Greenlight. If the Greenlight node already exists, neither are required.

- `LNBITS_BACKEND_WALLET_CLASS`: **BreezSdkWallet**
- `BREEZ_API_KEY`: ...
- `BREEZ_GREENLIGHT_SEED`: ...
- `BREEZ_GREENLIGHT_INVITE_CODE`: ...
- `BREEZ_GREENLIGHT_DEVICE_KEY`: /path/to/breezsdk/device.pem or Base64/Hex
- `BREEZ_GREENLIGHT_DEVICE_CERT`: /path/to/breezsdk/device.crt or Base64/Hex

### Breez Liquid SDK

This funding source leverages the [Breez SDK - Liquid](https://sdk-doc-liquid.breez.technology/) to manage all Lightning payments via submarine swaps on the Liquid network. To get started, simply provide a mnemonic seed phrase. The easiest way to generate one is by using a liquid wallet, such as [Blockstream Green](https://blockstream.com/green/). Once generated, you can copy the seed to your environment variable or enter it in the admin UI.

- `LNBITS_BACKEND_WALLET_CLASS`: **BreezLiquidSdkWallet**
- `BREEZ_LIQUID_SEED`: ...

Each submarine swap incurs service and on-chain fees. To account for these, you may need to increase the reserve fee in the admin UI by navigating to **Settings -> Funding**, or by setting the following environment variables:

- `LNBITS_RESERVE_FEE_MIN`: ...
- `LNBITS_RESERVE_FEE_PERCENT`: ...

### Cliche Wallet

- `CLICHE_ENDPOINT`: ws://127.0.0.1:12000

### Nostr Wallet Connect (NWC)

To use NWC as funding source in LNbits you'll need a pairing URL (also known as pairing secret) from a NWC service provider. You can find a list of providers [here](https://github.com/getAlby/awesome-nwc?tab=readme-ov-file#nwc-wallets).

You can configure Nostr Wallet Connect in the admin ui or using the following environment variables:

- `LNBITS_BACKEND_WALLET_CLASS`: **NWCWallet**
- `NWC_PAIRING_URL`: **nostr+walletconnect://...your...pairing...secret...**
