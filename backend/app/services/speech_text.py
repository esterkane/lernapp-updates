"""Deterministic reading text; displayed answers remain unchanged."""

import html
import re


def prepare(text: str) -> str:
    text = html.unescape(text)
    text = re.sub(r"!\[([^\]]*)\]\([^\n)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^\n)]*\)", r"\1", text)
    text = re.sub(r"^\s{0,3}(?:#{1,6}\s+|>\s*|[-*+]\s+)", "", text, flags=re.M)
    text = re.sub(r"\*\*(.*?)\*\*|__(.*?)__", lambda match: match[1] or match[2], text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\bz\.\s*B\.", "zum Beispiel", text)
    text = re.sub(r"\bd\.\s*h\.", "das heißt", text)
    text = re.sub(r"\bbzw\.", "beziehungsweise", text)
    text = re.sub(r"\bKonjunktiv\s+II\b", "Konjunktiv zwei", text)
    text = re.sub(r"\bKonjunktiv\s+I\b", "Konjunktiv eins", text)
    return re.sub(r"[ \t]+", " ", text).strip()
