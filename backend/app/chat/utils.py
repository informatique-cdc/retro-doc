"""Chat utilities.

This module provides utility functions for the chat.
"""

from app.chat.models import ChatMessageDocument
from app.chat.schemas import ChatMessageResponse
from app.chat.service import VariantPosition


def to_message_response(
    message: ChatMessageDocument, variant: VariantPosition | None
) -> ChatMessageResponse:
    """Build the API representation of a stored message.

    Args:
        message(ChatMessageDocument): The stored message.
        variant(VariantPosition | None): Its place among sibling answers,
            or `None` when the turn was answered only once.

    Returns:
        ChatMessageResponse: The message, with pager fields left `None` unless
            it has siblings. The endpoint excludes none-valued fields, so a
            thread with no regenerated answers serializes exactly as it did
            before.
    """
    return ChatMessageResponse(
        id=message.id,  # type: ignore[arg-type]
        role=message.role,
        content=message.content,
        sources=message.sources,
        variant_index=variant.index if variant else None,
        variant_count=variant.count if variant else None,
        prev_variant_id=variant.prev_id if variant else None,
        next_variant_id=variant.next_id if variant else None,
    )
