import asyncio

import bot_core as _core
from bot_core import *
from profile_feature import install
from runtime_main import run as _run


# Register profile handlers only after bot_core finished importing and all
# router/database/menu objects already exist.
install(_core)

# Re-export the patched menu for callers importing `bot` as a module.
main_menu_keyboard = _core.main_menu_keyboard


async def main():
    await _run(_core)


if __name__ == "__main__":
    asyncio.run(main())
