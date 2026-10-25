"""Offline translation used only to make fallback storyboard prompts SD1.5-friendly."""
from __future__ import annotations

import os


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
