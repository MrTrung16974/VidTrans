"""Offline translation used only to make fallback storyboard prompts SD1.5-friendly."""
from __future__ import annotations

import json
import logging
import os
import re

logger = logging.getLogger(__name__)

LLM_BATCH_LINES = 10
LLM_SYSTEM_PROMPT = (
    "You write prompts for an English-only image model. For each numbered Vietnamese story line, "
    "return one short English visual description (max 40 words) of what a single illustration "
    "should show: who is present, what they are doing, and where. Use generic descriptions such as "
    "\"a young man\" instead of character names. Do not add events that the line does not state. "
    "When a line is dialogue or an abstract idea, describe the speaker saying it (expression, "
    "gesture, listener) rather than trying to draw the idea itself. "
    "Return JSON {\"lines\": [...]} with exactly one string per input line, in the same order."
)
LLM_SCHEMA = {
    "type": "object",
    "properties": {"lines": {"type": "array", "items": {"type": "string"}}},
    "required": ["lines"],
}


class LocalVisualPromptTranslator:
    """Translate Vietnamese scene descriptions locally with Marian/OPUS-MT."""

    def __init__(self) -> None:
        self.model_id = os.environ.get(
            "VIDTRANS_VISUAL_TRANSLATION_MODEL", "Helsinki-NLP/opus-mt-vi-en"
        ).strip()
        self._tokenizer = None
        self._model = None

    def _load(self):
        if self._model is None:
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(self.model_id)
            self._model = AutoModelForSeq2SeqLM.from_pretrained(self.model_id)
            self._model.eval()
        return self._tokenizer, self._model

    def translate_lines(self, lines: list[str]) -> list[str]:
        import torch

        torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
        tokenizer, model = self._load()
        results: list[str] = []
        for start in range(0, len(lines), 8):
            batch = lines[start:start + 8]
            encoded = tokenizer(batch, return_tensors="pt", padding=True, truncation=True, max_length=256)
            with torch.inference_mode():
                generated = model.generate(**encoded, max_new_tokens=160, num_beams=1, renormalize_logits=True)
            results.extend(tokenizer.batch_decode(generated, skip_special_tokens=True))
        return results


class LLMVisualPromptTranslator:
    """Ask the storyboard LLM for English image prompts when it could not storyboard a chunk.

    Describing one line at a time is far easier than the full storyboard schema, so a small
    local model (qwen3:4b) that failed the storyboard still produces usable prompts here.
    """

    def __init__(self, llm) -> None:
        self.llm = llm

    def translate_lines(self, lines: list[str]) -> list[str]:
        results: list[str] = []
        for start in range(0, len(lines), LLM_BATCH_LINES):
            batch = lines[start:start + LLM_BATCH_LINES]
            numbered = "\n".join(f"{index}. {line}" for index, line in enumerate(batch, 1))
            answer = self.llm.generate_json(LLM_SYSTEM_PROMPT, numbered, LLM_SCHEMA)
            translated = _json_object(answer).get("lines")
            if not isinstance(translated, list) or len(translated) != len(batch):
                raise ValueError(f"LLM returned {len(translated or [])} prompts for {len(batch)} lines")
            results.extend(str(value).strip() for value in translated)
        return results


class ChainedVisualPromptTranslator:
    """Try each translator in order; the first complete answer wins."""

    def __init__(self, *translators) -> None:
        self.translators = [t for t in translators if t is not None]

    def translate_lines(self, lines: list[str]) -> list[str]:
        error: Exception | None = None
        for translator in self.translators:
            try:
                translated = translator.translate_lines(lines)
                if len(translated) == len(lines) and all(str(value).strip() for value in translated):
                    return translated
                error = ValueError(f"{type(translator).__name__} returned incomplete prompts")
            except Exception as exc:
                error = exc
            logger.info("Visual prompt translator %s failed (%s); trying the next one",
                        type(translator).__name__, type(error).__name__)
        raise error or ValueError("No visual prompt translator configured")


def _json_object(answer: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", str(answer or ""), flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("LLM answer has no JSON object")
    value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("LLM answer is not a JSON object")
    return value
