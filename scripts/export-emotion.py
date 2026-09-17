"""Build-time only: export and validate Chinese-Emotion-Small for CPU ONNX.

Run in an isolated export environment containing torch, transformers, onnx and
onnxruntime. None of torch/onnx/export tooling belongs in the runtime environment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

MODEL = "Johnson8187/Chinese-Emotion-Small"
REVISION = "a8b83db21e778e99d56058124eaab4ab99efe598"
SAMPLES = [
    "今天一起玩遊戲吧。",
    "你還好嗎？我很擔心你。",
    "我今天真的非常開心！",
    "你怎麼可以這樣對我！",
    "我很難過，想哭。",
    "這是什麼意思？",
    "哇，竟然成功了！",
    "這個味道好噁心。",
    "你好。",
    "謝謝你陪我聊天，我覺得好多了。",
    "Hello, nice to meet you.",
]


def export(output: Path, *, local_files_only: bool) -> None:
    import numpy as np
    import onnxruntime as ort
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    output.mkdir(parents=True, exist_ok=True)
    tokenizer = AutoTokenizer.from_pretrained(
        MODEL,
        revision=REVISION,
        local_files_only=local_files_only,
    )
    model = (
        AutoModelForSequenceClassification.from_pretrained(
            MODEL,
            revision=REVISION,
            local_files_only=local_files_only,
        )
        .cpu()
        .eval()
    )
    torch.set_num_threads(4)

    class Classifier(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.classifier = model

        def forward(self, input_ids, attention_mask):
            return self.classifier(
                input_ids=input_ids, attention_mask=attention_mask
            ).logits

    inputs = tokenizer(SAMPLES[:2], padding=True, return_tensors="pt")
    # Work in the destination filesystem; only replace the final model after QA.
    with tempfile.TemporaryDirectory(prefix="export-", dir=output) as temporary:
        raw = Path(temporary) / "fp32.onnx"
        with torch.no_grad():
            torch.onnx.export(
                Classifier().eval(),
                (inputs["input_ids"], inputs["attention_mask"]),
                str(raw),
                input_names=["input_ids", "attention_mask"],
                output_names=["logits"],
                opset_version=17,
                dynamo=False,
                dynamic_axes={
                    "input_ids": {0: "batch", 1: "sequence"},
                    "attention_mask": {0: "batch", 1: "sequence"},
                    "logits": {0: "batch"},
                },
            )
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        session = ort.InferenceSession(
            str(raw), sess_options=options, providers=["CPUExecutionProvider"]
        )
        evidence = []
        for text in SAMPLES:
            encoded = tokenizer(
                text, return_tensors="pt", truncation=True, max_length=512
            )
            with torch.no_grad():
                expected = model(**encoded).logits.numpy()
            actual = session.run(
                ["logits"],
                {
                    "input_ids": encoded["input_ids"].numpy(),
                    "attention_mask": encoded["attention_mask"].numpy(),
                },
            )[0]
            if not np.isfinite(actual).all() or actual.shape != (1, 8):
                raise RuntimeError("Invalid ONNX classifier output")
            if expected.argmax() != actual.argmax():
                raise RuntimeError(
                    f"ONNX classification differs on validation text: {text}"
                )
            np.testing.assert_allclose(actual, expected, rtol=1e-3, atol=1e-3)
            evidence.append(
                {
                    "text": text,
                    "class": int(actual.argmax()),
                    "max_logit_error": float(np.abs(expected - actual).max()),
                }
            )
        del session
        tokenizer.save_pretrained(output)
        os.replace(raw, output / "model.onnx")
    with (output / "model.onnx").open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "source": MODEL,
                "revision": REVISION,
                "format": "onnx-fp32",
                "sha256": digest,
                "validation": evidence,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Validated emotion ONNX: {output / 'model.onnx'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("build/models/emotion"))
    parser.add_argument("--local-files-only", action="store_true")
    args = parser.parse_args()
    export(args.output, local_files_only=args.local_files_only)
