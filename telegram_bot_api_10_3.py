"""Compatibility models for Bot API 10.3 rich-message buttons.

aiogram 3.30 supports rich messages, but its RichBlock union predates the
`buttons` block.  Without extending that union, both sendRichMessage responses
and callback updates from embedded buttons fail Pydantic validation.
"""

from typing import Annotated, Any, Literal, Optional, Union, get_args

from pydantic import Field

import aiogram.types as aiogram_types
from aiogram.client.default import Default
from aiogram.types import RichMessage
from aiogram.types.base import TelegramObject
from aiogram.types.rich_block_union import RichBlockUnion


class RichMessageButton10_3(TelegramObject):
    text: Any
    style: Literal["danger", "success", "primary", "link"] | None = None
    url: str | None = None
    callback_data: str | None = None
    web_app: dict[str, Any] | None = None
    login_url: dict[str, Any] | None = None
    switch_inline_query: str | None = None
    switch_inline_query_current_chat: str | None = None
    switch_inline_query_chosen_chat: dict[str, Any] | None = None
    copy_text: dict[str, Any] | None = None
    disabled: dict[str, Any] | None = None


class RichBlockButtons10_3(TelegramObject):
    type: Literal["buttons"]
    buttons: list[RichMessageButton10_3]
    align: Literal["left", "center", "right"] | None = None


def enable_bot_api_10_3_models() -> None:
    """Teach aiogram's update parser about the rich `buttons` block."""
    existing_union = get_args(RichBlockUnion)[0]
    compatible_union = Annotated[
        existing_union | RichBlockButtons10_3,
        Field(discriminator="type"),
    ]
    RichMessage.model_fields["blocks"].annotation = list[compatible_union]

    # aiogram resolves a large graph of forward references at import time.
    # Rebuild the graph twice so that nested Message schemas used in callback
    # queries and API responses pick up the extended RichMessage schema.
    namespace = {
        "List": list,
        "Optional": Optional,
        "Union": Union,
        "Literal": Literal,
        "Default": Default,
        **{name: getattr(aiogram_types, name) for name in aiogram_types.__all__},
    }
    for _ in range(2):
        for name in aiogram_types.__all__:
            model = getattr(aiogram_types, name)
            if hasattr(model, "model_rebuild"):
                model.model_rebuild(force=True, _types_namespace=namespace)
