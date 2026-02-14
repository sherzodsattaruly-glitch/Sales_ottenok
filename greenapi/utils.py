"""Shared utilities for greenapi module."""


def extract_quoted_text(quoted: dict) -> str:
    """Extract text from a quoted message dict (caption, textMessage, conversation)."""
    if "caption" in quoted and quoted["caption"]:
        return quoted["caption"]
    if "textMessage" in quoted and quoted["textMessage"]:
        return quoted["textMessage"]
    if "conversation" in quoted and quoted["conversation"]:
        return quoted["conversation"]
    return ""
