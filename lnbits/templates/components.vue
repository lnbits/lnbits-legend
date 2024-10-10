<template id="lnbits-wallet-list">
  <q-list
    v-if="user && user.wallets.length"
    dense
    class="lnbits-drawer__q-list"
  >
    <q-item-label header v-text="$t('wallets')"></q-item-label>
    <q-item
      v-for="wallet in wallets"
      :key="wallet.id"
      clickable
      :active="activeWallet && activeWallet.id === wallet.id"
      tag="a"
      :href="wallet.url"
    >
      <q-item-section side>
        <q-avatar
          size="md"
          :color="
            activeWallet && activeWallet.id === wallet.id
              ? $q.dark.isActive
                ? 'primary'
                : 'primary'
              : 'grey-5'
          "
        >
          <q-icon
            name="flash_on"
            :size="$q.dark.isActive ? '21px' : '20px'"
            :color="$q.dark.isActive ? 'blue-grey-10' : 'grey-3'"
          ></q-icon>
        </q-avatar>
      </q-item-section>
      <q-item-section>
        <q-item-label lines="1"
          ><span v-text="wallet.name"></span
        ></q-item-label>
        <q-item-label v-if="LNBITS_DENOMINATION != 'sats'" caption>
          <span
            v-text="
              parseFloat(String(wallet.live_fsat).replaceAll(',', '')) / 100
            "
          ></span
          >&nbsp;
          <span v-text="LNBITS_DENOMINATION"></span>
        </q-item-label>
        <q-item-label v-else caption>
          <span v-text="wallet.live_fsat"></span>&nbsp;
          <span v-text="LNBITS_DENOMINATION"></span>
        </q-item-label>
      </q-item-section>
      <q-item-section
        side
        v-show="activeWallet && activeWallet.id === wallet.id"
      >
        <q-icon name="chevron_right" color="grey-5" size="md"></q-icon>
      </q-item-section>
    </q-item>
    <q-item clickable @click="showForm = !showForm">
      <q-item-section side>
        <q-icon
          :name="showForm ? 'remove' : 'add'"
          color="grey-5"
          size="md"
        ></q-icon>
      </q-item-section>
      <q-item-section>
        <q-item-label
          lines="1"
          class="text-caption"
          v-text="$t('add_wallet')"
        ></q-item-label>
      </q-item-section>
    </q-item>
    <q-item v-if="showForm">
      <q-item-section>
        <q-form @submit="createWallet">
          <q-input filled dense v-model="walletName" label="Name wallet *">
            <template v-slot:append>
              <q-btn
                round
                dense
                flat
                icon="send"
                size="sm"
                @click="createWallet"
                :disable="walletName === ''"
              ></q-btn>
            </template>
          </q-input>
        </q-form>
      </q-item-section>
    </q-item>
  </q-list>
</template>

<template id="lnbits-extension-list">
  <q-list
    v-if="user && userExtensions.length > 0"
    dense
    class="lnbits-drawer__q-list"
  >
    <q-item-label header v-text="$t('extensions')"></q-item-label>
    <q-item
      v-for="extension in userExtensions"
      :key="extension.code"
      clickable
      :active="extension.isActive"
      tag="a"
      :href="extension.url"
    >
      <q-item-section side>
        <q-avatar size="md">
          <q-img :src="extension.tile" style="max-width: 20px"></q-img>
        </q-avatar>
      </q-item-section>
      <q-item-section>
        <q-item-label lines="1"
          ><span v-text="extension.name"></span>
        </q-item-label>
      </q-item-section>
      <q-item-section side v-show="extension.isActive">
        <q-icon name="chevron_right" color="grey-5" size="md"></q-icon>
      </q-item-section>
    </q-item>
    <div class="lt-md q-mt-xl q-mb-xl"></div>
  </q-list>
</template>

<template id="lnbits-manage">
  <q-list v-if="user" dense class="lnbits-drawer__q-list">
    <q-item-label header v-text="$t('manage')"></q-item-label>
    <div v-if="user.admin">
      <q-item
        v-if="showAdmin"
        clickable
        tag="a"
        href="/admin"
        :active="isActive('/admin')"
      >
        <q-item-section side>
          <q-icon
            name="admin_panel_settings"
            :color="isActive('/admin') ? 'primary' : 'grey-5'"
            size="md"
          ></q-icon>
        </q-item-section>
        <q-item-section>
          <q-item-label lines="1" v-text="$t('server')"></q-item-label>
        </q-item-section>
      </q-item>
      <q-item
        v-if="showNode"
        clickable
        tag="a"
        href="/node"
        :active="isActive('/node')"
      >
        <q-item-section side>
          <q-icon
            name="developer_board"
            :color="isActive('/node') ? 'primary' : 'grey-5'"
            size="md"
          ></q-icon>
        </q-item-section>
        <q-item-section>
          <q-item-label lines="1" v-text="$t('node')"></q-item-label>
        </q-item-section>
      </q-item>
      <q-item
        v-if="showUsers"
        clickable
        tag="a"
        href="/users"
        :active="isActive('/users')"
      >
        <q-item-section side>
          <q-icon
            name="groups"
            :color="isActive('/users') ? 'primary' : 'grey-5'"
            size="md"
          ></q-icon>
        </q-item-section>
        <q-item-section>
          <q-item-label lines="1" v-text="$t('users')"></q-item-label>
        </q-item-section>
      </q-item>
    </div>
    <q-item
      v-if="showExtensions"
      clickable
      tag="a"
      href="/extensions"
      :active="isActive('/extensions')"
    >
      <q-item-section side>
        <q-icon
          name="extension"
          :color="isActive('/extensions') ? 'primary' : 'grey-5'"
          size="md"
        ></q-icon>
      </q-item-section>
      <q-item-section>
        <q-item-label lines="1" v-text="$t('extensions')"></q-item-label>
      </q-item-section>
    </q-item>
  </q-list>
</template>

<template id="lnbits-payment-details">
  <div class="q-py-md" style="text-align: left">
    <div v-if="payment.tag" class="row justify-center q-mb-md">
      <q-badge v-if="hasTag" color="yellow" text-color="black">
        #<span v-text="payment.tag"></span>
      </q-badge>
    </div>

    <div class="row">
      <b v-text="$t('created')"></b>:
      <span v-text="payment.date"></span>
      (<span v-text="payment.dateFrom"></span>)
    </div>

    <div class="row" v-if="hasExpiry">
      <b v-text="$t('expiry')"></b>:
      <span v-text="payment.expirydate"></span>
      (<span v-text="payment.expirydateFrom"></span>)
    </div>

    <div class="row">
      <b v-text="$t('amount')"></b>:
      <span v-text="(payment.amount / 1000).toFixed(3)"></span>
      <span v-text="LNBITS_DENOMINATION"></span>
    </div>

    <div class="row">
      <b v-text="$t('fee')"></b>:
      <span v-text="(payment.fee / 1000).toFixed(3)"></span>
      <span v-text="LNBITS_DENOMINATION"></span>
    </div>

    <div class="text-wrap">
      <b style="white-space: nowrap" v-text="$t('payment_hash')"></b>:&nbsp;
      <span v-text="payment.payment_hash"></span>
      <q-icon
        name="content_copy"
        @click="copyText(payment.payment_hash)"
        size="1em"
        color="grey"
        class="q-mb-xs cursor-pointer"
      />
    </div>

    <div class="text-wrap">
      <b style="white-space: nowrap" v-text="$t('memo')"></b>:&nbsp;
      <span v-text="payment.memo"></span>
    </div>

    <div class="text-wrap" v-if="payment.webhook">
      <b style="white-space: nowrap" v-text="$t('webhook')"></b>:&nbsp;
      <span v-text="payment.webhook"></span>:&nbsp;<q-badge
        :color="webhookStatusColor"
        text-color="white"
      >
        <span v-text="webhookStatusText"></span>
      </q-badge>
    </div>

    <div class="text-wrap" v-if="hasPreimage">
      <b style="white-space: nowrap" v-text="$t('payment_proof')"></b>:&nbsp;
      <span v-text="payment.preimage"></span>
    </div>

    <div class="row" v-for="entry in extras">
      <q-badge v-if="hasTag" color="secondary" text-color="white">
        extra
      </q-badge>
      <b v-text="entry.key"></b>: <span v-text="entry.value"></span>
    </div>
    <div class="row" v-if="hasSuccessAction">
      <b>Success action</b>:
      <lnbits-lnurlpay-success-action
        :payment="payment"
        :success_action="payment.extra.success_action"
      ></lnbits-lnurlpay-success-action>
    </div>
  </div>
</template>

<template id="lnbits-dynamic-fields">
  <div v-if="formData">
    <div class="row q-mb-lg" v-for="o in options">
      <div class="col auto-width">
        <p v-if="o.options?.length" class="q-ml-xl">
          <span v-text="o.label || o.name"></span>
          <small v-if="o.description">
            (<span v-text="o.description"></span>)</small
          >
        </p>
        <lnbits-dynamic-fields
          v-if="o.options?.length"
          :options="o.options"
          v-model="formData[o.name]"
          @update:model-value="handleValueChanged"
          class="q-ml-xl"
        >
        </lnbits-dynamic-fields>
        <div v-else>
          <q-input
            v-if="o.type === 'number'"
            type="number"
            v-model="formData[o.name]"
            @update:model-value="handleValueChanged"
            :label="o.label || o.name"
            :hint="o.description"
            :rules="applyRules(o.required)"
            filled
            dense
          ></q-input>
          <q-input
            v-else-if="o.type === 'text'"
            type="textarea"
            rows="5"
            v-model="formData[o.name]"
            @update:model-value="handleValueChanged"
            :label="o.label || o.name"
            :hint="o.description"
            :rules="applyRules(o.required)"
            filled
            dense
          ></q-input>
          <q-input
            v-else-if="o.type === 'password'"
            v-model="formData[o.name]"
            @update:model-value="handleValueChanged"
            type="password"
            :label="o.label || o.name"
            :hint="o.description"
            :rules="applyRules(o.required)"
            filled
            dense
          ></q-input>
          <q-select
            v-else-if="o.type === 'select'"
            v-model="formData[o.name]"
            @update:model-value="handleValueChanged"
            :label="o.label || o.name"
            :hint="o.description"
            :options="o.values"
            :rules="applyRules(o.required)"
          ></q-select>
          <q-select
            v-else-if="o.isList"
            v-model.trim="formData[o.name]"
            @update:model-value="handleValueChanged"
            input-debounce="0"
            new-value-mode="add-unique"
            :label="o.label || o.name"
            :hint="o.description"
            :rules="applyRules(o.required)"
            filled
            multiple
            dense
            use-input
            use-chips
            multiple
            hide-dropdown-icon
          ></q-select>
          <div v-else-if="o.type === 'bool'">
            <q-item tag="label" v-ripple>
              <q-item-section avatar top>
                <q-checkbox
                  v-model="formData[o.name]"
                  @update:model-value="handleValueChanged"
                />
              </q-item-section>
              <q-item-section>
                <q-item-label
                  ><span v-text="o.label || o.name"></span
                ></q-item-label>
                <q-item-label caption>
                  <span v-text="o.description"></span>
                </q-item-label>
              </q-item-section>
            </q-item>
          </div>
          <q-input
            v-else-if="o.type === 'hidden'"
            v-model="formData[o.name]"
            type="text"
            style="display: none"
            :rules="applyRules(o.required)"
          ></q-input>
          <div v-else-if="o.type === 'chips'">
            <lnbits-dynamic-chips
              v-model="formData[o.name]"
              @update:model-value="handleValueChanged"
            ></lnbits-dynamic-chips>
          </div>
          <q-input
            v-else
            v-model="formData[o.name]"
            @update:model-value="handleValueChanged"
            :hint="o.description"
            :label="o.label || o.name"
            :rules="applyRules(o.required)"
            filled
            dense
          ></q-input>
        </div>
      </div>
    </div>
  </div>
</template>

<template id="lnbits-dynamic-chips">
  <q-input
    filled
    v-model="chip"
    @keydown.enter.prevent="addChip"
    type="text"
    label="wss://...."
    hint="Add relays"
    class="q-mb-md"
  >
    <q-btn @click="addChip" dense flat icon="add"></q-btn>
  </q-input>
  <div>
    <q-chip
      v-for="(chip, i) in chips"
      :key="chip"
      removable
      @remove="removeChip(i)"
      color="primary"
      text-color="white"
      :label="chip"
    >
    </q-chip>
  </div>
</template>

<template id="lnbits-notifications-btn">
  <q-btn
    v-if="g.user.wallets"
    :disabled="!this.isSupported"
    dense
    flat
    round
    @click="toggleNotifications()"
    :icon="this.isSubscribed ? 'notifications_active' : 'notifications_off'"
    size="sm"
    type="a"
  >
    <q-tooltip v-if="this.isSupported && !this.isSubscribed"
      >Subscribe to notifications</q-tooltip
    >
    <q-tooltip v-if="this.isSupported && this.isSubscribed"
      >Unsubscribe from notifications</q-tooltip
    >
    <q-tooltip v-if="this.isSupported && this.isPermissionDenied">
      Notifications are disabled,<br />please enable or reset permissions
    </q-tooltip>
    <q-tooltip v-if="!this.isSupported"
      >Notifications are not supported</q-tooltip
    >
  </q-btn>
</template>

<template id="lnbits-update-balance">
  <q-btn v-if="admin" round color="primary" icon="add" size="sm">
    <q-popup-edit class="bg-accent text-white" v-slot="scope" v-model="credit">
      <q-input
        filled
        :label="$t('credit_label', {denomination: denomination})"
        :hint="$t('credit_hint')"
        v-model="scope.value"
        dense
        autofocus
        @keyup.enter="updateBalance(scope.value)"
      >
        <template v-slot:append>
          <q-icon name="edit" />
        </template>
      </q-input>
    </q-popup-edit>
    <q-tooltip>Topup Wallet</q-tooltip>
  </q-btn>
</template>

<template id="lnbits-qrcode">
  <div class="qrcode__wrapper">
    <qrcode-vue
      :value="value"
      level="Q"
      render-as="svg"
      margin="1"
      size="350"
      class="rounded-borders"
    ></qrcode-vue>
    <img class="qrcode__image" :src="logo" alt="qrcode icon" />
  </div>
</template>

<template id="lnbits-lnurlpay-success-action">
  <div>
    <p
      class="q-mb-sm"
      v-text="success_action.message || success_action.description"
    ></p>
    <code
      v-if="decryptedValue"
      class="text-h6 q-mt-sm q-mb-none"
      v-text="decryptedValue"
    ></code>
    <p v-else-if="success_action.url" class="text-h6 q-mt-sm q-mb-none">
      <a
        target="_blank"
        style="color: inherit"
        :href="success_action.url"
        v-text="success_action.url"
      ></a>
    </p>
  </div>
</template>

<template id="payment-list">
  <q-card
    :style="
      $q.screen.lt.md
        ? {
            background: $q.screen.lt.md ? 'none !important' : '',
            boxShadow: $q.screen.lt.md ? 'none !important' : '',
            marginTop: $q.screen.lt.md ? '0px !important' : ''
          }
        : ''
    "
  >
    <q-card-section>
      <div class="row items-center no-wrap q-mb-sm">
        <div class="col">
          <h5
            class="text-subtitle1 q-my-none"
            :v-text="$t('transactions')"
          ></h5>
        </div>
        <div class="gt-sm col-auto">
          <q-btn-dropdown
            outline
            persistent
            class="q-mr-sm"
            color="grey"
            :label="$t('export_csv')"
            split
            @click="exportCSV(false)"
          >
            <q-list>
              <q-item>
                <q-item-section>
                  <q-input
                    @keydown.enter="addFilterTag"
                    filled
                    dense
                    v-model="exportTagName"
                    type="text"
                    label="Payment Tags"
                    class="q-pa-sm"
                  >
                    <q-btn @click="addFilterTag" dense flat icon="add"></q-btn>
                  </q-input>
                </q-item-section>
              </q-item>
              <q-item v-if="exportPaymentTagList.length">
                <q-item-section>
                  <div>
                    <q-chip
                      v-for="tag in exportPaymentTagList"
                      :key="tag"
                      removable
                      @remove="removeExportTag(tag)"
                      color="primary"
                      text-color="white"
                      :label="tag"
                    ></q-chip>
                  </div>
                </q-item-section>
              </q-item>

              <q-item>
                <q-item-section>
                  <q-btn
                    v-close-popup
                    outline
                    color="grey"
                    @click="exportCSV(true)"
                    label="Export to CSV with details"
                  ></q-btn>
                </q-item-section>
              </q-item>
            </q-list>
          </q-btn-dropdown>
          <payment-chart :wallet="wallet" />
        </div>
      </div>
      <q-input
        :style="
          $q.screen.lt.md
            ? {
                display: mobileSimple ? 'none !important' : ''
              }
            : ''
        "
        filled
        dense
        clearable
        v-model="paymentsTable.search"
        debounce="300"
        :placeholder="$t('search_by_tag_memo_amount')"
        class="q-mb-md"
      >
      </q-input>
      <q-table
        dense
        flat
        :rows="paymentsOmitter"
        :row-key="paymentTableRowKey"
        :columns="paymentsTable.columns"
        :no-data-label="$t('no_transactions')"
        :filter="paymentsTable.search"
        :loading="paymentsTable.loading"
        :hide-header="mobileSimple"
        :hide-bottom="mobileSimple"
        v-model:pagination="paymentsTable.pagination"
        @request="fetchPayments"
      >
        <template v-slot:header="props">
          <q-tr :props="props">
            <q-th auto-width></q-th>
            <q-th
              v-for="col in props.cols"
              :key="col.name"
              :props="props"
              v-text="col.label"
            ></q-th>
          </q-tr>
        </template>
        <template v-slot:body="props">
          <q-tr :props="props">
            <q-td auto-width class="text-center">
              <q-icon
                v-if="props.row.isPaid"
                size="14px"
                :name="props.row.isOut ? 'call_made' : 'call_received'"
                :color="props.row.isOut ? 'pink' : 'green'"
                @click="props.expand = !props.expand"
              ></q-icon>
              <q-icon
                v-else-if="props.row.isFailed"
                name="warning"
                color="yellow"
                @click="props.expand = !props.expand"
              >
                <q-tooltip><span>failed</span></q-tooltip>
              </q-icon>
              <q-icon
                v-else
                name="settings_ethernet"
                color="grey"
                @click="props.expand = !props.expand"
              >
                <q-tooltip><span v-text="$t('pending')"></span></q-tooltip>
              </q-icon>
            </q-td>
            <q-td
              key="time"
              :props="props"
              style="white-space: normal; word-break: break-all"
            >
              <q-badge v-if="props.row.tag" color="yellow" text-color="black">
                <a
                  v-text="'#' + props.row.tag"
                  class="inherit"
                  :href="['/', props.row.tag].join('')"
                ></a>
              </q-badge>
              <span v-text="props.row.memo"></span>
              <br />

              <i>
                <span v-text="props.row.dateFrom"></span>
                <q-tooltip><span v-text="props.row.date"></span></q-tooltip>
              </i>
            </q-td>
            <q-td
              auto-width
              key="amount"
              v-if="denomination != 'sats'"
              :props="props"
              class="col1"
              v-text="
                parseFloat(String(props.row.fsat).replaceAll(',', '')) / 100
              "
            >
            </q-td>
            <q-td class="col2" auto-width key="amount" v-else :props="props">
              <span v-text="props.row.fsat"></span>
              <br />
              <i v-if="props.row.extra.wallet_fiat_currency">
                <span
                  v-text="
                    formatCurrency(
                      props.row.extra.wallet_fiat_amount,
                      props.row.extra.wallet_fiat_currency
                    )
                  "
                ></span>
                <br />
              </i>
              <i v-if="props.row.extra.fiat_currency">
                <span
                  v-text="
                    formatCurrency(
                      props.row.extra.fiat_amount,
                      props.row.extra.fiat_currency
                    )
                  "
                ></span>
              </i>
            </q-td>

            <q-dialog v-model="props.expand" :props="props" position="top">
              <q-card class="q-pa-lg q-pt-xl lnbits__dialog-card">
                <div class="text-center q-mb-lg">
                  <div v-if="props.row.isIn && props.row.isPending">
                    <q-icon name="settings_ethernet" color="grey"></q-icon>
                    <span v-text="$t('invoice_waiting')"></span>
                    <lnbits-payment-details
                      :payment="props.row"
                    ></lnbits-payment-details>
                    <div v-if="props.row.bolt11" class="text-center q-mb-lg">
                      <a :href="'lightning:' + props.row.bolt11">
                        <lnbits-qrcode
                          :value="'lightning:' + props.row.bolt11.toUpperCase()"
                        ></lnbits-qrcode>
                      </a>
                    </div>
                    <div class="row q-mt-lg">
                      <q-btn
                        outline
                        color="grey"
                        @click="copyText(props.row.bolt11)"
                        :label="$t('copy_invoice')"
                      ></q-btn>
                      <q-btn
                        v-close-popup
                        flat
                        color="grey"
                        class="q-ml-auto"
                        :label="$t('close')"
                      ></q-btn>
                    </div>
                  </div>
                  <div v-else-if="props.row.isOut && props.row.isPending">
                    <q-icon name="settings_ethernet" color="grey"></q-icon>
                    <span v-text="$t('outgoing_payment_pending')"></span>
                    <lnbits-payment-details
                      :payment="props.row"
                    ></lnbits-payment-details>
                  </div>
                  <div v-else-if="props.row.isPaid && props.row.isIn">
                    <q-icon
                      size="18px"
                      :name="'call_received'"
                      :color="'green'"
                    ></q-icon>
                    <span v-text="$t('payment_received')"></span>
                    <lnbits-payment-details
                      :payment="props.row"
                    ></lnbits-payment-details>
                  </div>
                  <div v-else-if="props.row.isPaid && props.row.isOut">
                    <q-icon
                      size="18px"
                      :name="'call_made'"
                      :color="'pink'"
                    ></q-icon>
                    <span v-text="$t('payment_sent')"></span>
                    <lnbits-payment-details
                      :payment="props.row"
                    ></lnbits-payment-details>
                  </div>
                  <div v-else-if="props.row.isFailed">
                    <q-icon name="warning" color="yellow"></q-icon>
                    <span>Payment failed</span>
                    <lnbits-payment-details
                      :payment="props.row"
                    ></lnbits-payment-details>
                  </div>
                </div>
              </q-card>
            </q-dialog>
          </q-tr>
        </template>
      </q-table>
    </q-card-section>
  </q-card>
</template>

<template id="lnbits-extension-rating">
  <div style="margin-bottom: 3px">
    <q-rating v-model="rating" size="1.5em" :max="5" color="primary"
      ><q-tooltip>
        <span v-text="$t('extension_rating_soon')"></span> </q-tooltip
    ></q-rating>
  </div>
</template>

<template id="lnbits-extension-settings-form">
  <q-form v-if="settings" @submit="updateSettings" class="q-gutter-md">
    <lnbits-dynamic-fields
      :options="options"
      v-model="settings"
    ></lnbits-dynamic-fields>
    <div class="row q-mt-lg">
      <q-btn v-close-popup unelevated color="primary" type="submit"
        >Update</q-btn
      >
      <q-btn v-close-popup unelevated color="danger" @click="resetSettings"
        >Reset</q-btn
      >
      <slot name="actions"></slot>
    </div>
  </q-form>
</template>

<template id="lnbits-extension-settings-btn-dialog">
  <q-btn
    v-if="options"
    unelevated
    @click="show = true"
    color="primary"
    icon="settings"
    class="float-right"
  >
    <q-dialog v-model="show" position="top">
      <q-card class="q-pa-lg q-pt-xl lnbits__dialog-card">
        <lnbits-extension-settings-form
          :options="options"
          :adminkey="adminkey"
          :endpoint="endpoint"
        >
          <template v-slot:actions>
            <q-btn v-close-popup flat color="grey" class="q-ml-auto"
              >Close</q-btn
            >
          </template>
        </lnbits-extension-settings-form>
      </q-card>
    </q-dialog>
  </q-btn>
</template>

<template id="lnbits-funding-sources">
  <div class="funding-sources">
    <h6 class="q-mt-xl q-mb-md">Funding Sources</h6>
    <div class="row">
      <div class="col-12">
        <p>Active Funding<small> (Requires server restart)</small></p>
        <q-select
          filled
          v-model="formData.lnbits_backend_wallet_class"
          hint="Select the active funding wallet"
          :options="sortedAllowedFundingSources"
          :option-label="item => getFundingSourceLabel(item)"
        ></q-select>
      </div>
    </div>
    <q-list
      class="q-mt-md"
      v-for="(fund, idx) in allowedFundingSources"
      :key="idx"
    >
      <div
        v-if="
          fundingSources.get(fund) &&
          fund === formData.lnbits_backend_wallet_class
        "
      >
        <div
          class="row"
          v-for="([key, prop], i) in Object.entries(fundingSources.get(fund))"
          :key="i"
        >
          <div class="col-12">
            <q-input
              filled
              type="text"
              class="q-mt-sm"
              v-model="formData[key]"
              :label="prop.label"
              :hint="prop.hint"
            ></q-input>
          </div>
        </div>
      </div>
    </q-list>
  </div>
</template>

<template id="payment-chart">
  <span id="payment-chart">
    <q-btn dense flat round icon="show_chart" color="grey" @click="showChart">
      <q-tooltip>
        <span v-text="$t('chart_tooltip')"></span>
      </q-tooltip>
    </q-btn>

    <q-dialog v-model="paymentsChart.show" position="top">
      <q-card class="q-pa-sm" style="width: 800px; max-width: unset">
        <q-card-section>
          <div class="row q-gutter-sm justify-between">
            <div class="text-h6">Payments Chart</div>
            <q-select
              label="Group"
              filled
              dense
              v-model="paymentsChart.group"
              style="min-width: 120px"
              :options="paymentsChart.groupOptions"
            >
            </q-select>
          </div>
          <canvas ref="canvas" width="600" height="400"></canvas>
        </q-card-section>
      </q-card>
    </q-dialog>
  </span>
</template>
