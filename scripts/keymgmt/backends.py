"""
backends.py — FIDO2 backend 介面與真實裝置實作（P1-12a #227）

介面刻意收斂為兩個操作，讓測試能以 mock backend 完整覆蓋協定邏輯
（keystore wrap/unwrap、多 token 冗餘、錯誤路徑），真實 CTAP2 細節
全部隔離在 RealFido2Backend：

- register(label, pin)             → credential_id（在 token 上建 hmac-secret 憑證）
- hmac_secret(credential_id, salt, pin) → 32 bytes（CTAP2 hmac-secret 輸出，
  同一 (credential, salt) 永遠回同一值 — wrap key 的來源）

⚠ RealFido2Backend 尚未經實體金鑰驗證（硬體驗收 → issue #230）。
  Windows 上直接 CTAP HID 需 Administrator 權限；真機驗收建議 Mac/Linux。
"""

from __future__ import annotations

import secrets


class KeyBackendError(Exception):
    """FIDO2 backend 操作失敗（找不到裝置 / 憑證不符 / 使用者取消）。"""


RP_ID = "ics-command.local"
RP_NAME = "ICS Command keymgmt"
USER_NAME = "ics-master-key"


class RealFido2Backend:
    """python-fido2（Yubico，BSD-2）實作。lazy import — 未裝 fido2 套件時，
    mock 路徑（測試）與 env fallback（dev）完全不受影響。"""

    def __init__(self) -> None:
        try:
            from fido2.client import DefaultClientDataCollector, Fido2Client, UserInteraction
            from fido2.hid import CtapHidDevice
        except ImportError as e:
            raise KeyBackendError(
                "需要 python-fido2 套件（pip install fido2）— "
                "dev / 測試場景請改用 ICS_MASTER_KEY env fallback"
            ) from e
        self._Fido2Client = Fido2Client
        self._CtapHidDevice = CtapHidDevice
        self._DefaultClientDataCollector = DefaultClientDataCollector
        self._UserInteraction = UserInteraction

    def _client(self, pin: str | None):
        devices = list(self._CtapHidDevice.list_devices())
        if not devices:
            raise KeyBackendError("找不到 FIDO2 裝置 — 請插入 token（Windows 需系統管理員權限）")

        ui_cls = self._UserInteraction

        class _PinUI(ui_cls):  # type: ignore[misc, valid-type]
            def prompt_up(self) -> None:
                print("[!] 請觸碰 token …")

            def request_pin(self, permissions, rp_id):
                return pin

            def request_uv(self, permissions, rp_id) -> bool:
                return True

        return self._Fido2Client(
            devices[0],
            client_data_collector=self._DefaultClientDataCollector(f"https://{RP_ID}"),
            user_interaction=_PinUI(),
        )

    def register(self, label: str, pin: str | None) -> bytes:
        """在當前插入的 token 上建立 hmac-secret 憑證，回傳 credential_id。"""
        client = self._client(pin)
        result = client.make_credential(
            {
                "rp": {"id": RP_ID, "name": RP_NAME},
                "user": {"id": label.encode("utf-8"), "name": USER_NAME},
                "challenge": secrets.token_bytes(32),
                "pubKeyCredParams": [{"type": "public-key", "alg": -7}],
                "extensions": {"hmacCreateSecret": True},
            }
        )
        ext = result.extension_results or {}
        if not ext.get("hmacCreateSecret"):
            raise KeyBackendError("token 不支援 hmac-secret extension（需 CTAP2，如 YubiKey 5）")
        cred = result.attestation_object.auth_data.credential_data
        if cred is None:
            raise KeyBackendError("token 未回傳 credential data")
        return bytes(cred.credential_id)

    def hmac_secret(self, credential_id: bytes, salt: bytes, pin: str | None) -> bytes:
        """以指定憑證 + salt 取得 hmac-secret 輸出（32 bytes，wrap key 來源）。"""
        client = self._client(pin)
        try:
            result = client.get_assertion(
                {
                    "rpId": RP_ID,
                    "challenge": secrets.token_bytes(32),
                    "allowCredentials": [{"type": "public-key", "id": credential_id}],
                    "extensions": {"hmacGetSecret": {"salt1": salt}},
                }
            ).get_response(0)
        except Exception as e:  # fido2 例外族系於真機驗收（#230）時收斂
            raise KeyBackendError(f"hmac-secret 取得失敗：{e}") from e
        ext = result.extension_results or {}
        output = ext.get("hmacGetSecret", {}).get("output1")
        if not output:
            raise KeyBackendError("token 未回傳 hmac-secret 輸出（憑證可能非本 token 所有）")
        return bytes(output)
