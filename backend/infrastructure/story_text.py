"""Read and normalise uploaded story text (TXT or DOCX) without extra dependencies."""
from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from xml.etree import ElementTree

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
_WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_SENTENCE_END = re.compile(r"(?<=[.!?…。！？])[\"'”’»)\]]*\s+")


class StoryTextError(ValueError):
    pass


def decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if encoding == "utf-16" and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
            continue
        return text
    # Legacy Vietnamese Windows files; cp1258 decodes every byte, so it is the last resort.
    return data.decode("cp1258", errors="replace")


def docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > 50 * 1024 * 1024:
                raise StoryTextError("File DOCX quá lớn")
            root = ElementTree.fromstring(archive.read(info))
    except (KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise StoryTextError("File DOCX không hợp lệ") from exc
    paragraphs = []
    for paragraph in root.iter(f"{_WORD_NS}p"):
        pieces = []
        for node in paragraph.iter():
            if node.tag == f"{_WORD_NS}t":
                pieces.append(node.text or "")
            elif node.tag == f"{_WORD_NS}tab":
                pieces.append(" ")
            elif node.tag in {f"{_WORD_NS}br", f"{_WORD_NS}cr"}:
                pieces.append("\n")
        paragraphs.append("".join(pieces))
    return "\n".join(paragraphs)


def read_story_upload(filename: str, data: bytes) -> str:
    if len(data) > MAX_UPLOAD_BYTES:
        raise StoryTextError("File truyện vượt quá 5 MB")
    name = (filename or "").lower()
    if name.endswith(".docx"):
        return normalize_story_text(docx_text(data))
    if name.endswith((".txt", ".md")) or not name:
        return normalize_story_text(decode_text(data))
    raise StoryTextError("Chỉ hỗ trợ file .txt, .md hoặc .docx")


def normalize_story_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in text.split("\n")]
    paragraphs: list[str] = []
    current: list[str] = []
    for line in lines:
        if line:
            current.append(line)
        elif current:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs)


def split_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for paragraph in text.split("\n\n"):
        sentences.extend(part.strip() for part in _SENTENCE_END.split(paragraph) if part.strip())
    return sentences


def chunk_story(text: str, max_chars: int = 3500) -> list[str]:
    """Split at paragraph (then sentence) boundaries so no chunk cuts a sentence."""
    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        pieces = [paragraph] if len(paragraph) <= max_chars else split_sentences(paragraph)
        for piece in pieces:
            while len(piece) > max_chars:  # A single run-on "sentence" longer than a chunk.
                cut = piece.rfind(" ", 0, max_chars)
                cut = cut if cut > max_chars // 2 else max_chars
                if current:
                    chunks.append(current)
                    current = ""
                chunks.append(piece[:cut].strip())
                piece = piece[cut:].strip()
            joiner = "\n\n" if current else ""
            if current and len(current) + len(joiner) + len(piece) > max_chars:
                chunks.append(current)
                current, joiner = "", ""
            current += joiner + piece
    if current:
        chunks.append(current)
    return chunks
