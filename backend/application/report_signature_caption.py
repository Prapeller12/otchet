"""Exactly two plain-text lines for all report PDF renderers."""

from typing import Any


def signature_caption(verification: dict[str, Any]) -> tuple[str, str]:
    status = verification.get("status")
    if status == "VERIFIED":
        return (
            f"Подтверждено: {verification['signer_name']} | {verification['signed_at']} | Ed25519",
            f"Ключ: {verification['key_fingerprint']} | Подпись: {verification['signature']}",
        )
    captions = {
        "STALE": "Данные изменены после подписи. Требуется повторное подтверждение.",
        "INVALID": "Подпись недействительна: проверка целостности не пройдена.",
        "LEGACY": "Прежняя отметка без криптографической подписи. Подтвердите данные заново.",
    }
    return (
        captions.get(str(status), "Данные не подтверждены"),
        f"SHA-256 данных: {verification['snapshot_sha256']}",
    )
