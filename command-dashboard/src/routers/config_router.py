# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
from fastapi import APIRouter, Request

from auth.service import validate_session
from repositories.config_repo import get_config, set_config
from schemas.admin import ConfigIn

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("/{key}")
def read_config(key: str, request: Request):
    validate_session(request)
    return {"key": key, "value": get_config(key)}


@router.post("/{key}")
def write_config(key: str, body: ConfigIn, request: Request):
    sess = validate_session(request)
    set_config(key, body.value, sess["username"])
    return {"ok": True}
