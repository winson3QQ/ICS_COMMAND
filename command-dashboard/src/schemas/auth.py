from pydantic import BaseModel


class LoginIn(BaseModel):
    username: str
    pin:      str


class ChangeInitialPinIn(BaseModel):
    current_pin: str
    new_pin:     str
