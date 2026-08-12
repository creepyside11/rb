# Emerald AI payment bot

Telegram worker for `@emeraldairobot`, built on aiogram 3. It accepts only personal links created by the Emerald AI account dashboard, creates RUB-denominated Crypto Bot invoices, verifies them through Crypto Pay API, and atomically credits the shared PostgreSQL balance.

Users can select a ready-made package or enter an exact token amount. The rate is 1,000,000 tokens per ₽1; fractional prices are rounded up to one kopeck and shown before invoice creation.

## Environment

Set these variables on the bot hosting:

```env
BOT_TOKEN=...
DATABASE_URL=...
CRYPTOBOT_TOKEN=...
```

`DATABASE_URL` must be exactly the same PostgreSQL database used by the Emerald website. Do not commit a real `.env` file.

`CRYPTOBOT_TOKEN` is the API token of a mainnet Crypto Pay app created in `@CryptoBot`. The app must not have an IP allowlist unless the hosting egress IP is included.

`BOT_TOKEN` and `CRYPTOBOT_TOKEN` are different credentials. On startup the worker calls Crypto Pay `getMe`; an invalid token or blocked hosting IP therefore appears immediately in the hosting log instead of failing later with a generic invoice error.

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
