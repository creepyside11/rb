# Emerald AI payment bot

Telegram worker for `@emeraldairobot`, built on aiogram 3. It accepts only personal links created by the Emerald AI account dashboard and supports automatic Crypto Bot and Platega payments.

Users can select a ready-made package or enter an exact token amount. The administrator can change the RUB price per 1,000,000 tokens from `/admin`; the setting is stored in the database and applies immediately to packages and custom amounts. Fractional totals are rounded up to one kopeck and shown before invoice creation.

Pending invoices are reconciled with Crypto Pay and Platega every 5 seconds. A successful payment is credited automatically and the user receives a Telegram confirmation. The Platega flow never asks for a receipt or an administrator approval.

The user-facing method is named «СБП Платега». It creates a Platega v2 payment link without `paymentMethod`, so the hosted Platega page lets the payer choose any method enabled for the merchant instead of redirecting directly to SBP.

## Environment

Set these variables on the bot hosting:

```env
BOT_TOKEN=...
DATABASE_URL=...
CRYPTOBOT_TOKEN=...
PLATEGA_MERCHANT_ID=...
PLATEGA_API_KEY=...
PLATEGA_RETURN_URL=https://t.me/emeraldairobot
PLATEGA_FAILED_URL=https://t.me/emeraldairobot
ADMIN_ID=7973988177
```

`DATABASE_URL` must be exactly the same PostgreSQL database used by the Emerald website. Do not commit a real `.env` file.

`CRYPTOBOT_TOKEN` is the API token of a mainnet Crypto Pay app created in `@CryptoBot`. The app must not have an IP allowlist unless the hosting egress IP is included.

`BOT_TOKEN` and `CRYPTOBOT_TOKEN` are different credentials. On startup the worker calls Crypto Pay `getMe`; an invalid token or blocked hosting IP therefore appears immediately in the hosting log instead of failing later with a generic invoice error.

`PLATEGA_MERCHANT_ID` and `PLATEGA_API_KEY` are sent only in the documented `X-MerchantId` and `X-Secret` request headers. Configure both or neither; a partial configuration stops startup instead of exposing a broken payment button. Keep real values in the hosting environment, never in the repository.

Set the invoice lifetime to **60 minutes** in the Platega merchant settings. The public [create-payment-link API](https://docs.platega.io/%D1%81%D0%BE%D0%B7%D0%B4%D0%B0%D0%BD%D0%B8%D0%B5-%D0%BF%D0%BB%D0%B0%D1%82%D0%B5%D0%B6%D0%BD%D0%BE%D0%B9-%D1%81%D1%81%D1%8B%D0%BB%D0%BA%D0%B8-%D0%B1%D0%B5%D0%B7-%D0%B7%D0%B0%D0%B4%D0%B0%D0%BD%D0%BD%D0%BE%D0%B3%D0%BE-%D0%BC%D0%B5%D1%82%D0%BE%D0%B4%D0%B0-33845703e0) returns `expiresIn` but does not accept a lifetime field. The worker also stops polling and marks its local payment expired after 60 minutes; it logs a warning when the provider returns a different lifetime.

`ADMIN_ID` is the only Telegram account allowed to open `/admin`, inspect Crypto Bot and Platega statistics and transactions, browse separate clickable website-user and Telegram-bot-user lists, change the token price, and run broadcasts. Telegram usernames and names are refreshed whenever a user interacts with the bot. If omitted, the configured default is `7973988177`.

## Run

```bash
pip install -r requirements.txt
python bot.py
```

The process is a long-running Telegram polling worker, not a web service. Run exactly one replica, because two polling instances with the same bot token conflict. On platforms with process types use the included `Procfile`; Docker hosting can use the included `Dockerfile`.

## Payment safety

- A personal link is bound to the first Telegram account that opens it.
- Package values are selected only from a server-side allowlist.
- A paid status, transaction ID, private payload, RUB currency, and amount are re-read from the payment provider before crediting.
- PostgreSQL row locks and the final `paid` state make repeated checks idempotent.
- Platega credits only the documented `CONFIRMED` status; repeated polls cannot credit the balance twice.
