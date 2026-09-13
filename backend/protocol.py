from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


PROTOCOL_VERSION = 1
MAX_LINE_BYTES = 1024 * 1024


class ProtocolError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Request:
    request_id: str
    method: str
    params: dict[str, Any]


def decode_request(line: bytes) -> Request:
    if len(line) > MAX_LINE_BYTES:
        raise ProtocolError("LINE_TOO_LARGE", "協定訊息超過 1 MiB")
    try:
        payload = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("INVALID_JSON", "無法解析 UTF-8 JSON") from exc

    if not isinstance(payload, dict):
        raise ProtocolError("INVALID_REQUEST", "request 必須是物件")
    if payload.get("v") != PROTOCOL_VERSION:
        raise ProtocolError("VERSION_MISMATCH", "不支援的協定版本")
    if payload.get("type") != "request":
        raise ProtocolError("INVALID_REQUEST", "訊息類型必須是 request")

    request_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params", {})
    if not isinstance(request_id, str) or not request_id:
        raise ProtocolError("INVALID_REQUEST", "request id 不可為空")
    if not isinstance(method, str) or not method:
        raise ProtocolError("INVALID_REQUEST", "method 不可為空")
    if not isinstance(params, dict):
        raise ProtocolError("INVALID_REQUEST", "params 必須是物件")
    return Request(request_id=request_id, method=method, params=params)


def response_ok(request_id: str, result: object) -> dict[str, Any]:
    return {
        "v": PROTOCOL_VERSION,
        "type": "response",
        "id": request_id,
        "ok": True,
        "result": result,
    }


def response_error(
    request_id: str | None,
    code: str,
    message: str,
    *,
    retryable: bool = False,
) -> dict[str, Any]:
    return {
        "v": PROTOCOL_VERSION,
        "type": "response",
        "id": request_id,
        "ok": False,
        "error": {"code": code, "message": message, "retryable": retryable},
    }


def encode_message(message: dict[str, Any]) -> bytes:
    encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    ) + b"\n"
    if len(encoded) > MAX_LINE_BYTES:
        raise ProtocolError("LINE_TOO_LARGE", "協定訊息超過 1 MiB")
    return encoded
