import unittest

from aiogram.types import Update

from telegram_bot_api_10_3 import enable_bot_api_10_3_models


class TelegramBotApi10_3Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        enable_bot_api_10_3_models()

    def test_callback_from_rich_message_button_is_parsed(self):
        update = Update.model_validate({
            "update_id": 100,
            "callback_query": {
                "id": "callback-1",
                "from": {
                    "id": 123,
                    "is_bot": False,
                    "first_name": "Test",
                },
                "chat_instance": "instance-1",
                "data": "show:packages",
                "message": {
                    "message_id": 42,
                    "date": 1_787_590_800,
                    "chat": {
                        "id": 123,
                        "type": "private",
                        "first_name": "Test",
                    },
                    "rich_message": {
                        "blocks": [
                            {"type": "paragraph", "text": "Emerald AI"},
                            {
                                "type": "buttons",
                                "align": "center",
                                "buttons": [{
                                    "text": "Купить токены",
                                    "style": "primary",
                                    "callback_data": "show:packages",
                                }],
                            },
                        ],
                    },
                },
            },
        })

        callback = update.callback_query
        self.assertIsNotNone(callback)
        self.assertEqual(callback.data, "show:packages")
        self.assertEqual(callback.message.rich_message.blocks[1].type, "buttons")
        self.assertEqual(
            callback.message.rich_message.blocks[1].buttons[0].callback_data,
            "show:packages",
        )


if __name__ == "__main__":
    unittest.main()
