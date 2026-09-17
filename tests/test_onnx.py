import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from backend.emotion import EmotionService
from backend.nllb import NllbTranslator, MODEL_FILES
from backend.settings import default_settings, validate_settings


class OnnxInferenceTests(unittest.TestCase):
    def test_unsupported_translation_model_is_rejected(self):
        settings = default_settings()
        settings["translation"]["model"] = "unsupported-model"
        with self.assertRaises(ValueError):
            validate_settings(settings)

    def test_download_allowlist_excludes_fp16_weights(self):
        self.assertEqual(
            [x for x in MODEL_FILES if x.endswith(".onnx")],
            ["encoder_model_int8.onnx", "decoder_model_int8.onnx"],
        )

    def test_nllb_prefix_mask_language_and_eos(self):
        engine = NllbTranslator()
        tokenizer = Mock()
        tokenizer.unk_token_id, tokenizer.bos_token_id = 3, 0
        tokenizer.pad_token_id, tokenizer.eos_token_id = 1, 2
        tokenizer.convert_tokens_to_ids.side_effect = [256168, 256047]
        tokenizer.return_value = {
            "input_ids": np.array([[7, 2]]),
            "attention_mask": np.array([[1, 1]]),
        }
        tokenizer.decode.return_value = "你好"
        engine._tokenizer = tokenizer
        engine._encoder, engine._decoder = Mock(), Mock()
        hidden = np.zeros((1, 2, 4), dtype=np.float32)
        engine._encoder.run.return_value = [hidden]
        prefixes = []

        def decode(_outputs, feeds):
            prefixes.append(feeds["input_ids"].tolist()[0])
            self.assertIs(feeds["encoder_hidden_states"], hidden)
            self.assertEqual(feeds["input_ids"].dtype, np.int64)
            scores = np.zeros((1, len(prefixes[-1]), 10), dtype=np.float32)
            scores[0, -1, 5 if len(prefixes) == 1 else 2] = 10
            return [scores]

        engine._decoder.run.side_effect = decode
        self.assertEqual(engine.translate("hello", "eng_Latn", "zho_Hant"), "你好")
        self.assertEqual(prefixes, [[2, 256168], [2, 256168, 5]])
        self.assertEqual(tokenizer.src_lang, "eng_Latn")
        tokenizer.decode.assert_called_once_with([5], skip_special_tokens=True)

    def test_emotion_uses_numpy_logits_and_only_declared_inputs(self):
        service = EmotionService(
            on_model_status=Mock(), on_result=Mock(), on_error=Mock()
        )
        service._tokenizer = Mock(
            return_value={
                "input_ids": np.array([[4, 5]]),
                "attention_mask": np.array([[1, 1]]),
                "token_type_ids": np.array([[0, 0]]),
            }
        )
        service._model = Mock()
        service._model.get_inputs.return_value = [
            SimpleNamespace(name="input_ids"),
            SimpleNamespace(name="attention_mask"),
        ]
        service._model.run.return_value = [np.array([[0, 0, 5, 0, 0, 0, 0, 0]])]
        self.assertEqual(service._predict("開心"), 2)
        self.assertEqual(service._tokenizer.call_args.kwargs["return_tensors"], "np")
        self.assertEqual(
            set(service._model.run.call_args.args[1]), {"input_ids", "attention_mask"}
        )

    def test_onnx_sessions_are_explicitly_cpu(self):
        from backend.runtime import cpu_session

        with patch("onnxruntime.InferenceSession") as create:
            cpu_session("model.onnx")
        self.assertEqual(create.call_args.kwargs["providers"], ["CPUExecutionProvider"])
