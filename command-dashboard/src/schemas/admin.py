
from pydantic import BaseModel


class AccountCreateIn(BaseModel):
    username:     str
    pin:          str
    role:         str = "操作員"
    role_detail:  str | None = None
    display_name: str | None = None


class AccountStatusIn(BaseModel):
    status: str  # active / suspended


class PinResetIn(BaseModel):
    new_pin: str


class AdminPinIn(BaseModel):
    new_pin: str


class RoleUpdateIn(BaseModel):
    role:        str
    role_detail: str | None = None


class PiNodeCreateIn(BaseModel):
    unit_id: str
    label:   str


class ConfigIn(BaseModel):
    value: str
