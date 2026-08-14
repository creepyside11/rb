# Emerald AI payment bot

Telegram worker for `@emeraldairobot`, built on aiogram 3. It accepts only personal links created by the Emerald AI account dashboard and supports automatic Crypto Bot invoices plus manually reviewed SBP payments.

Users can select a ready-made package or enter an exact token amount. The rate is 1,000,000 tokens per ₽1; fractional prices are rounded up to one kopeck and shown before invoice creation.

Pending invoices are reconciled with Crypto Pay every 5 seconds. A successful payment is credited automatically and the user receives a Telegram confirmation; the manual check button remains as an immediate fallback.

## Environment

Set these variables on the bot hosting:

```env
BOT_TOKEN=...
DATABASE_URL=...
CRYPTOBOT_TOKEN=...
ADMIN_ID=7973988177
```

`DATABASE_URL` must be exactly the same PostgreSQL database used by the Emerald website. Do not commit a real `.env` file.

`CRYPTOBOT_TOKEN` is the API token of a mainnet Crypto Pay app created in `@CryptoBot`. The app must not have an IP allowlist unless the hosting egress IP is included.

`BOT_TOKEN` and `CRYPTOBOT_TOKEN` are different credentials. On startup the worker calls Crypto Pay `getMe`; an invalid token or blocked hosting IP therefore appears immediately in the hosting log instead of failing later with a generic invoice error.

`ADMIN_ID` is the only Telegram account allowed to open `/admin`, review SBP receipts, inspect statistics and users, and run broadcasts. If omitted, the configured default is `7973988177`.

For SBP, the bot shows the configured phone, bank and recipient, receives a screenshot or PDF receipt, and forwards it to the administrator with one-time «Одобрить» / «Отклонить» actions.

## Run

```bash
pip install -r requirements.txt
python bot.py
```

The process is a long-running Telegram polling worker, not a web service. Run exactly one replica, because two polling instances with the same bot token conflict. On platforms with process types use the included `Procfile`; Docker hosting can use the included `Dockerfile`.

## Payment safety

- A personal link is bound to the first Telegram account that opens it.
- Package values are selected only from a server-side allowlist.
- A paid status, invoice ID, private payload, fiat currency, and RUB amount are re-read from Crypto Pay before crediting.
- PostgreSQL row locks and the final `paid` state make repeated checks idempotent.
- SBP approval also uses row locks, so a repeated admin click cannot credit the balance twice.
