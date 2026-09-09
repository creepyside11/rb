import asyncio

import bot_core as _core
from bot_core import *
from profile_feature import install
from runtime_main import run as _run
from runtime_patches import apply as _apply_runtime_patches


# Keep the original exported helper for backwards-compatible unit tests and
# imports, but patch bot_core itself: all real handlers resolve their globals
# from bot_core and therefore use the menu with the Profile button.
_public_main_menu_keyboard = main_menu_keyboard
install(_core)
_apply_runtime_patches(_core)
main_menu_keyboard = _public_main_menu_keyboard
runtime_main_menu_keyboard = _core.main_menu_keyboard


async def main():
    await _run(_core)


if __name__ == "__main__":
    asyncio.run(main())
