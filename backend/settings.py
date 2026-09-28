from __future__ import annotations

from copy import deepcopy
from typing import Any
from backend.nllb import MODEL_ID


SPEECH_MODELS = {
    "auto",
    "tiny",
    "base",
    "small",
    "medium",
    "large-v3",
    "large-v3-turbo",
}
TRANSLATION_MODELS = {MODEL_ID}
OVERLAY_ANCHORS = {"head", "leftHand", "rightHand"}


DEFAULT_SETTINGS: dict[str, Any] = {
    "schemaVersion": 1,
    "audio": {
        "deviceId": "default",
        "speakerDeviceId": "speaker:default",
        "autoStart": False,
    },
    "speech": {"model": "auto", "language": "zh"},
    "translation": {
        "enabled": False,
        "provider": "onnx",
        "model": MODEL_ID,
        "sourceLanguage": "zh",
        "targetLanguage": "en",
        "endpoint": "http://127.0.0.1:5000",
        "apiKey": "",
        "deeplPlan": "free",
        "deeplApiKey": "",
    },
    "osc": {"enabled": True, "host": "127.0.0.1", "port": 9000},
    "emotion": {
        "enabled": False,
        "parameter": "/avatar/parameters/v2t_sync_emo",
    },
    "privacy": {"saveAudio": False, "saveTranscripts": False},
    "overlay": {
        "enabled": False,
        "anchor": "head",
        "scale": 1.0,
        "offsetX": 0.0,
        "offsetY": -0.35,
        "offsetZ": -1.5,
        "pitch": 0.0,
        "yaw": 0.0,
        "roll": 0.0,
        "opacity": 0.9,
        "holdSeconds": 8,
        "showMicrophone": False,
        "showSpeaker": True,
        "watchMode": False,
        # Row-major 3x4 resting pose on the anchor, captured by calibration.
        "basis": [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0],
    },
}

_ALLOWED_KEYS = {
    "audio": {"deviceId", "speakerDeviceId", "autoStart"},
    "speech": {"model", "language"},
    "translation": {
        "enabled",
        "provider",
        "model",
        "sourceLanguage",
        "targetLanguage",
        "endpoint",
        "apiKey",
        "deeplPlan",
        "deeplApiKey",
    },
    "osc": {"enabled", "host", "port"},
    "emotion": {"enabled", "parameter"},
    "privacy": {"saveAudio", "saveTranscripts"},
    "overlay": {
        "enabled",
        "anchor",
        "scale",
        "offsetX",
        "offsetY",
        "offsetZ",
        "pitch",
        "yaw",
        "roll",
        "opacity",
        "holdSeconds",
        "showMicrophone",
        "showSpeaker",
        "watchMode",
        "basis",
    },
}


class SettingsError(ValueError):
    """Raised when a settings document or patch is invalid."""


def default_settings() -> dict[str, Any]:
    return deepcopy(DEFAULT_SETTINGS)


def validate_settings(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SettingsError("設定必須是物件")
    if value.get("schemaVersion") != 1:
        raise SettingsError("不支援的設定版本")

    result = default_settings()
    for section in _ALLOWED_KEYS:
        incoming = value.get(section)
        if not isinstance(incoming, dict):
            raise SettingsError(f"{section} 必須是物件")
        if set(incoming) != _ALLOWED_KEYS[section]:
            raise SettingsError(f"{section} 欄位不完整或包含未知欄位")
        result[section] = deepcopy(incoming)

    _validate_values(result)
    return result


def apply_patch(current: dict[str, Any], patch: object) -> dict[str, Any]:
    if not isinstance(patch, dict) or not patch:
        raise SettingsError("設定更新必須是非空物件")
    if "schemaVersion" in patch:
        raise SettingsError("不能透過 patch 變更設定版本")

    candidate = deepcopy(current)
    for section, values in patch.items():
        if section not in _ALLOWED_KEYS or not isinstance(values, dict):
            raise SettingsError(f"未知或無效的設定區段: {section}")
        unknown = set(values) - _ALLOWED_KEYS[section]
        if unknown:
            raise SettingsError(f"{section} 包含未知欄位: {sorted(unknown)[0]}")
        candidate[section].update(deepcopy(values))

    _validate_values(candidate)
    return candidate


def _validate_values(settings: dict[str, Any]) -> None:
    audio = settings["audio"]
    speech = settings["speech"]
    translation = settings["translation"]
    osc = settings["osc"]
    emotion = settings["emotion"]
    privacy = settings["privacy"]
    overlay = settings["overlay"]

    _require_string(audio["deviceId"], "audio.deviceId")
    _require_string(audio["speakerDeviceId"], "audio.speakerDeviceId")
    _require_bool(audio["autoStart"], "audio.autoStart")
    speech_model = _require_string(speech["model"], "speech.model")
    if speech_model not in SPEECH_MODELS:
        raise SettingsError("不支援的語音辨識模型")
    _require_string(speech["language"], "speech.language")
    _require_bool(translation["enabled"], "translation.enabled")
    _require_string(translation["provider"], "translation.provider")
    translation_model = _require_string(translation["model"], "translation.model")
    if translation_model not in TRANSLATION_MODELS:
        raise SettingsError("不支援的本機翻譯模型")
    _require_string(translation["sourceLanguage"], "translation.sourceLanguage")
    _require_string(translation["targetLanguage"], "translation.targetLanguage")
    _require_string(translation["endpoint"], "translation.endpoint")
    if not isinstance(translation["apiKey"], str):
        raise SettingsError("translation.apiKey 必須是字串")
    if translation["deeplPlan"] not in {"free", "pro"}:
        raise SettingsError("translation.deeplPlan 必須是 free 或 pro")
    if not isinstance(translation["deeplApiKey"], str):
        raise SettingsError("translation.deeplApiKey 必須是字串")
    _require_bool(osc["enabled"], "osc.enabled")
    _require_string(osc["host"], "osc.host")
    if isinstance(osc["port"], bool) or not isinstance(osc["port"], int):
        raise SettingsError("osc.port 必須是整數")
    if not 1 <= osc["port"] <= 65535:
        raise SettingsError("osc.port 必須介於 1 與 65535")
    _require_bool(emotion["enabled"], "emotion.enabled")
    parameter = _require_string(emotion["parameter"], "emotion.parameter")
    if not parameter.startswith("/") or " " in parameter:
        raise SettingsError("emotion.parameter 必須是有效的 OSC 位址")
    _require_bool(privacy["saveAudio"], "privacy.saveAudio")
    _require_bool(privacy["saveTranscripts"], "privacy.saveTranscripts")
    _require_bool(overlay["enabled"], "overlay.enabled")
    if overlay["anchor"] not in OVERLAY_ANCHORS:
        raise SettingsError("overlay.anchor 必須是 head、leftHand 或 rightHand")
    _require_number(overlay["scale"], "overlay.scale", 0.2, 2.0)
    _require_number(overlay["offsetX"], "overlay.offsetX", -1.0, 1.0)
    _require_number(overlay["offsetY"], "overlay.offsetY", -1.0, 1.0)
    _require_number(overlay["offsetZ"], "overlay.offsetZ", -5.0, 1.0)
    for axis in ("pitch", "yaw", "roll"):
        _require_number(overlay[axis], f"overlay.{axis}", -180.0, 180.0)
    _require_number(overlay["opacity"], "overlay.opacity", 0.2, 1.0)
    if isinstance(overlay["holdSeconds"], bool) or not isinstance(
        overlay["holdSeconds"], int
    ):
        raise SettingsError("overlay.holdSeconds 必須是整數")
    if not 2 <= overlay["holdSeconds"] <= 60:
        raise SettingsError("overlay.holdSeconds 必須介於 2 與 60")
    _require_bool(overlay["showMicrophone"], "overlay.showMicrophone")
    _require_bool(overlay["showSpeaker"], "overlay.showSpeaker")
    _require_bool(overlay["watchMode"], "overlay.watchMode")
    basis = overlay["basis"]
    if not isinstance(basis, list) or len(basis) != 12:
        raise SettingsError("overlay.basis 必須是 12 個數字")
    for value in basis:
        _require_number(value, "overlay.basis", -10.0, 10.0)


def _require_string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SettingsError(f"{path} 必須是非空字串")
    return value


def _require_bool(value: object, path: str) -> None:
    if not isinstance(value, bool):
        raise SettingsError(f"{path} 必須是布林值")


def _require_number(value: object, path: str, minimum: float, maximum: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SettingsError(f"{path} 必須是數字")
    if not minimum <= value <= maximum:
        raise SettingsError(f"{path} 必須介於 {minimum:g} 與 {maximum:g}")
