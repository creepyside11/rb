import asyncio

import bot_core as _core
from bot_core import *
from profile_feature import install


# Register the profile handlers only after bot_core has finished importing and
# all of its router, database helpers and menu functions already exist.
install(_core)

# Re-export the patched menu for tests and for code importing `bot` as a module.
main_menu_keyboard = _core.main_menu_keyboard
main = _core.main


if __name__ == "__main__":
    asyncio.run(_core.main())
