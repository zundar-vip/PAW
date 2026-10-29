import asyncio, logging, signal
from aiohttp import web

from config import WEBHOOK_PORT, SEED_ADMIN_IDS, PAWNOVA_WEB_PORT
import db, bot1, bot2, hook, server

logging.basicConfig(
    format="%(asctime)s │ %(name)-18s │ %(levelname)-7s │ %(message)s",
    datefmt="%H:%M:%S", level=logging.INFO)
log = logging.getLogger("run")


async def main():
    db.init(); log.info("DB ready")
    for a in SEED_ADMIN_IDS:
        db.add_admin(a, "seed"); log.info(f"Admin: {a}")

    app1 = bot1.build_app(); app2 = bot2.build_app()

    hook.set_credit_callback(bot1.credit_from_webhook)
    hook.set_notify_admin_callback(bot1.notify_admin_tx)

    poll_task   = asyncio.create_task(hook.poll_sepay_loop(),  name="sepay-poll")
    expire_task = asyncio.create_task(hook.expire_qr_loop(app1.bot, bot1.kb_main), name="qr-expire")

    # webhook server (SePay)
    wh_server = hook.build_server()
    wh_runner = web.AppRunner(wh_server)
    await wh_runner.setup()
    await web.TCPSite(wh_runner, "0.0.0.0", WEBHOOK_PORT).start()
    log.info(f"SePay webhook → :{WEBHOOK_PORT}")

    # PAWNOVA web server (tool + admin + API)
    paw_server = server.build_paw_server()
    paw_runner = web.AppRunner(paw_server)
    await paw_runner.setup()
    await web.TCPSite(paw_runner, "0.0.0.0", PAWNOVA_WEB_PORT).start()
    log.info(f"PAWNOVA web → http://localhost:{PAWNOVA_WEB_PORT}")

    await app1.initialize(); await app2.initialize()
    await app1.start();      await app2.start()
    await app1.updater.start_polling(drop_pending_updates=True)
    await app2.updater.start_polling(drop_pending_updates=True)
    log.info("Bots polling ✓")

    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    await stop.wait()
    poll_task.cancel(); expire_task.cancel()
    for t in (poll_task, expire_task):
        try: await t
        except: pass
    await app1.updater.stop(); await app2.updater.stop()
    await app1.stop(); await app2.stop()
    await app1.shutdown(); await app2.shutdown()
    await wh_runner.cleanup(); await paw_runner.cleanup()
    log.info("Shutdown ✓")


if __name__ == "__main__":
    asyncio.run(main())