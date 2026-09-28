from __future__ import annotations

import threading

from backend.runtime import cpu_session
from backend.i18n import t

MODEL_ID = "venddair/nllb-200-distilled-600M-onnx"
MODEL_REVISION = "714bc338a10f51a2f5567ff355aab76f21de8a91"
# Greedy decoding can fall into loops ("ha ha ha ..."). Block repeated
# n-grams and bound the output by the input length instead of failing.
NO_REPEAT_NGRAM = 3
MAX_NEW_TOKENS = 256


def max_new_tokens(source_tokens: int) -> int:
    return min(MAX_NEW_TOKENS, source_tokens * 3 + 16)


def repeated_ngram_tokens(generated: list[int], size: int = NO_REPEAT_NGRAM) -> set[int]:
    """Tokens that would complete an n-gram already present in `generated`."""
    if len(generated) < size:
        return set()
    prefix = tuple(generated[-(size - 1) :])
    return {
        generated[index + size - 1]
        for index in range(len(generated) - size + 1)
        if tuple(generated[index : index + size - 1]) == prefix
    }


MODEL_FILES = [
    "encoder_model_int8.onnx",
    "decoder_model_int8.onnx",
    "config.json",
    "generation_config.json",
    "sentencepiece.bpe.model",
    "special_tokens_map.json",
    "tokenizer_config.json",
]


class NllbTranslator:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tokenizer = None
        self._encoder = None
        self._decoder = None

    def _load(self) -> None:
        from huggingface_hub import snapshot_download
        from transformers import AutoTokenizer
        from pathlib import Path

        directory = Path(
            snapshot_download(
                MODEL_ID,
                revision=MODEL_REVISION,
                allow_patterns=MODEL_FILES,
            )
        )
        tokenizer = AutoTokenizer.from_pretrained(
            str(directory),
            use_fast=False,
            local_files_only=True,
        )
        encoder = cpu_session(directory / "encoder_model_int8.onnx")
        decoder = cpu_session(directory / "decoder_model_int8.onnx")
        self._tokenizer, self._encoder, self._decoder = tokenizer, encoder, decoder

    def translate(self, text: str, source: str, target: str) -> str:
        import numpy as np

        # NLLB's source-language setter mutates the tokenizer's special tokens.
        with self._lock:
            if self._tokenizer is None:
                self._load()
            tokenizer = self._tokenizer
            target_id = tokenizer.convert_tokens_to_ids(target)
            source_id = tokenizer.convert_tokens_to_ids(source)
            if target_id == tokenizer.unk_token_id or source_id == tokenizer.unk_token_id:
                raise ValueError(t("backend.nllb.unsupportedLanguage"))
            tokenizer.src_lang = source
            encoded = tokenizer(
                text, return_tensors="np", truncation=True, max_length=512
            )
            mask = np.asarray(encoded["attention_mask"], dtype=np.int64)
            hidden = self._encoder.run(
                None,
                {
                    "input_ids": np.asarray(encoded["input_ids"], dtype=np.int64),
                    "attention_mask": mask,
                },
            )[0]
            # This export has no past-key-value inputs; decode the full prefix.
            # It expects EOS-as-BOS followed by the target language token.
            ids = [tokenizer.eos_token_id, target_id]
            for _ in range(max_new_tokens(int(mask.sum()))):
                logits = self._decoder.run(
                    None,
                    {
                        "input_ids": np.asarray([ids], dtype=np.int64),
                        "encoder_attention_mask": mask,
                        "encoder_hidden_states": hidden,
                    },
                )[0]
                scores = logits[0, -1].copy()
                if not np.isfinite(scores).all():
                    raise RuntimeError(t("backend.nllb.invalidOutput"))
                # These are control tokens, never generated translation text.
                scores[tokenizer.bos_token_id] = -np.inf
                scores[tokenizer.pad_token_id] = -np.inf
                for banned in repeated_ngram_tokens(ids[2:]):
                    scores[banned] = -np.inf
                token = int(np.argmax(scores))
                if token == tokenizer.eos_token_id:
                    break
                ids.append(token)
            # Reaching the length bound keeps the text translated so far.
            translated = tokenizer.decode(ids[2:], skip_special_tokens=True).strip()
            if not translated:
                raise RuntimeError(t("backend.nllb.emptyOutput"))
            return translated

    def close(self) -> None:
        with self._lock:
            self._tokenizer = self._encoder = self._decoder = None
