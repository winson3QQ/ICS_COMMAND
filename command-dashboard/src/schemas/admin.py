from pydantic import BaseModel


class AccountCreateIn(BaseModel):
    username: str
    pin: str
    role: str = "操作員"
    role_detail: str | None = None
    display_name: str | None = None


class AccountStatusIn(BaseModel):
    status: str  # active / suspended


class PinResetIn(BaseModel):
    new_pin: str


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
