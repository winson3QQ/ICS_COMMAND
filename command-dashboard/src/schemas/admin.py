from typing import Literal

from pydantic import BaseModel

# #343 紅藍隔離 admin 分類請求體
_Faction = Literal["blue", "red", "neutral"]


class FactionClassifyIn(BaseModel):
    """admin 把連線 client（裝置 self-SA uid）分類成紅/藍/中立。exercise_id 省略=實戰池。"""

    client_key: str
    faction: _Faction
    callsign: str | None = None
    exercise_id: int | None = None


class FactionOverrideIn(BaseModel):
    """admin 對單一 entity 手動點陣營（無 producer 可歸屬者，如 iTAK 繪圖）。"""

    uid: str
    faction: _Faction


class AccountCreateIn(BaseModel):
    # #348-F5 P2b：移除 pin 欄——admin 不再自設，由後端 generate_temp_pin 產生並一次性回傳。
    username: str
    role: str = "操作員"
    role_detail: str | None = None
    display_name: str | None = None


class AccountStatusIn(BaseModel):
    status: str  # active / suspended


# PinResetIn 已移除（#348-F5 P2b）：reset_pin 改系統產隨機臨時 PIN、不收 body。


class AdminPinIn(BaseModel):
    new_pin: str


class RoleUpdateIn(BaseModel):
    role: str
    role_detail: str | None = None


class DisplayNameUpdateIn(BaseModel):
    display_name: str


class PiNodeCreateIn(BaseModel):
    unit_id: str
    label: str


class AccountCertBindIn(BaseModel):
    # #275 wave 3：綁定一張裝置 client cert 的 CN（= step-ca 簽發時的 subject CN）
    cert_cn: str
    label: str | None = None


class ConfigIn(BaseModel):
    value: str


class SuspendAllIn(BaseModel):
    # OP-1（#153）：不可逆批次停權的明確確認字串（後端強制，不依賴前端 dialog）。
    confirm: str


class RetentionToggleIn(BaseModel):
    """#207：軌跡 PII TTL 清理 runtime 開關。"""

    enabled: bool
