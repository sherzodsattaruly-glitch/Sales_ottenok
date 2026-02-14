"""
Pydantic модели для вебхуков Green API.
"""

from pydantic import BaseModel
from typing import Optional


class SenderData(BaseModel):
    chatId: str
    sender: str
    senderName: Optional[str] = ""


class TextMessageData(BaseModel):
    textMessage: str


class QuotedMessageData(BaseModel):
    textMessage: Optional[str] = None
    caption: Optional[str] = None


class QuoteMessageInfo(BaseModel):
    stanzaId: str
    participant: str
    quotedMessage: Optional[dict] = None  # Raw dict to avoid recursion issues if complex


class ExtendedTextMessageData(BaseModel):
    text: str
    quotedMessage: Optional[dict] = None
    stanzaId: Optional[str] = None
    participant: Optional[str] = None


class CaptionMessageData(BaseModel):
    caption: Optional[str] = ""


class MessageData(BaseModel):
    typeMessage: str
    textMessageData: Optional[TextMessageData] = None
    extendedTextMessageData: Optional[ExtendedTextMessageData] = None
    quotedMessageData: Optional[ExtendedTextMessageData] = None  # Same structure as extendedTextMessage
    imageMessageData: Optional[CaptionMessageData] = None
    videoMessageData: Optional[CaptionMessageData] = None


class WebhookPayload(BaseModel):
    typeWebhook: str
    senderData: Optional[SenderData] = None
    messageData: Optional[MessageData] = None
