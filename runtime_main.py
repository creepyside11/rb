from __future__ import annotations

import asyncio


async def run(core) -> None:
    """Start the Telegram worker without letting optional payment providers kill it."""
    core.load_dotenv()
    bot_token = core.os.getenv("BOT_TOKEN", "").strip()
    crypto_token = core.os.getenv("CRYPTOBOT_TOKEN", "").strip()
    platega_merchant_id = core.os.getenv("PLATEGA_MERCHANT_ID", "").strip()
    platega_api_key = core.os.getenv("PLATEGA_API_KEY", "").strip()
    platega_return_url = core.os.getenv(
        "PLATEGA_RETURN_URL", "https://t.me/emeraldairobot"
    ).strip()
    platega_failed_url = core.os.getenv(
        "PLATEGA_FAILED_URL", "https://t.me/emeraldairobot"
    ).strip()

    try:
        admin_id = int(
            core.os.getenv("ADMIN_ID", str(core.DEFAULT_ADMIN_ID)).strip()
        )
    except ValueError as error:
        raise RuntimeError("ADMIN_ID must be a Telegram numeric user ID") from error

    if not bot_token:
        raise RuntimeError("BOT_TOKEN is not configured")

    if crypto_token and bot_token == crypto_token:
        core.logger.error(
            "CRYPTOBOT_TOKEN equals BOT_TOKEN; Crypto Bot payments are disabled"
        )
        crypto_token = ""

    if bool(platega_merchant_id) != bool(platega_api_key):
        core.logger.error(
            "Platega credentials are incomplete; Platega payments are disabled"
        )
        platega_merchant_id = ""
        platega_api_key = ""

    engine, session_factory = core.database_from_environment()
    await asyncio.to_thread(core.Base.metadata.create_all, engine)
    await asyncio.to_thread(core.initialize_bot_records, session_factory)

    crypto = None
    if crypto_token:
        candidate = core.CryptoPayClient(crypto_token)
        try:
            crypto_app = await candidate.get_me()
        except Exception as error:
            core.logger.error(
                "Crypto Pay startup check failed; Crypto payments are disabled: %s",
                error,
            )
            await candidate.close()
        else:
            crypto = candidate
            core.logger.info(
                "Crypto Pay authenticated: app_id=%s name=%s",
                crypto_app.get("app_id"),
                crypto_app.get("name"),
            )
    else:
        core.logger.warning(
            "CRYPTOBOT_TOKEN is not configured; Crypto Bot payments are unavailable"
        )

    platega = (
        core.PlategaClient(
            platega_merchant_id,
            platega_api_key,
            platega_return_url,
            platega_failed_url,
        )
        if platega_merchant_id
        else None
    )
    if platega is None:
        core.logger.warning(
            "Platega credentials are not configured; Platega payments are unavailable"
        )

    bot = core.Bot(
        bot_token,
        default=core.DefaultBotProperties(parse_mode=core.ParseMode.HTML),
    )
    dispatcher = core.Dispatcher()
    dispatcher.update.outer_middleware(core.BotUserTrackingMiddleware())
    dispatcher.include_router(core.router)
    dispatcher.errors.register(core.handle_error)

    stop_reconciliation = asyncio.Event()
    reconciliation_task = None
    if crypto is not None or platega is not None:
        reconciliation_task = asyncio.create_task(
            core.payment_reconciliation_loop(
                bot,
                session_factory,
                crypto,
                platega,
                stop_reconciliation,
            ),
            name="payment-reconciliation",
        )

    try:
        await bot.delete_webhook(drop_pending_updates=False)
        core.logger.info("Telegram polling is starting")
        await dispatcher.start_polling(
            bot,
            session_factory=session_factory,
            crypto=crypto,
            platega=platega,
            admin_id=admin_id,
            allowed_updates=dispatcher.resolve_used_update_types(),
            close_bot_session=False,
        )
    finally:
        stop_reconciliation.set()
        if reconciliation_task is not None:
            try:
                await reconciliation_task
            except Exception:
                core.logger.exception("Payment reconciliation task stopped with an error")
        if crypto is not None:
            await crypto.close()
        if platega is not None:
            await platega.close()
        await bot.session.close()
        engine.dispose()
