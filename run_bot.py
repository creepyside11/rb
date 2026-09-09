import asyncio

import bot
from profile_feature import install


install(bot)


if __name__ == "__main__":
    asyncio.run(bot.main())
