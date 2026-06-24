"""core/pin_policy.py — #348-F5 P1：PIN/密語強度策略（NIST 800-63B 對齊）。

定位（見 security_policies §2.8.1）：本系統 PIN 是 mTLS 裝置憑證的 **activation secret**
（NIST 800-63B §5.1.7 多因子密碼學裝置）→ 允許數字 PIN（≥6），同時放開長密語（≤128、任意
可列印）供高權帳號。規則對齊 NIST：
  - 長度 **6–128**（原 4-6 純數字 → 提下限、開上限、不限數字）
  - **無組成規則、無定期強制換**（NIST 反對 composition rules / forced rotation）
  - **拒絕可預測值**：全同字元、連續序列、== 帳號名、常見弱值（本地清單，**離線、無外部 API**，
    對齊離線部署 + 無中國供應鏈紅線）

**不溯及既往**：登入只驗 hash、不重驗長度，故現有 4-6 位 PIN 照常可登入；本策略**僅在
「設定/變更 PIN」時套用**（create / reset / change-initial / admin-PIN 四出口）。
完全提升熵（強制長密語）非本批——本批提下限 + 擋可預測 + 開放長密語能力。
"""

from fastapi import HTTPException

MIN_LEN = 6
MAX_LEN = 128

# 常見弱值（本地清單，離線）。短於 MIN_LEN 者本就被長度擋（如 1234/0000），故此處列 ≥6 位
# 常見 PIN/密碼 + 鍵盤序。全同/連續另由 pattern 檢查通捕，不需逐一列舉。
_COMMON_WEAK = frozenset(
    {
        "123456",
        "654321",
        "123123",
        "112233",
        "121212",
        "123321",
        "159753",
        "147258",
        "789456",
        "456789",
        "112358",
        "102030",
        "password",
        "passw0rd",
        "p@ssw0rd",
        "letmein",
        "welcome",
        "iloveyou",
        "admin123",
        "qwerty",
        "qwertyuiop",
        "1q2w3e",
        "1q2w3e4r",
        "1qaz2wsx",
        "zaq12wsx",
        "abc123",
        "qazwsx",
        "trustno1",
        "changeme",
        "secret",
    }
)


def _is_run(s: str) -> bool:
    """全升或全降連續序列（123456 / 654321 / abcdef）。"""
    asc = all(ord(s[i + 1]) - ord(s[i]) == 1 for i in range(len(s) - 1))
    desc = all(ord(s[i]) - ord(s[i + 1]) == 1 for i in range(len(s) - 1))
    return asc or desc


def validate_pin_strength(pin: str, username: str | None = None) -> None:
    """設定/變更 PIN 時的強度閘。違反 → HTTPException(422)；通過 → None。

    呼叫端：create_acct / reset_pin / change_pin（Admin PIN）/ change_initial_pin。
    """
    if not isinstance(pin, str) or len(pin) < MIN_LEN:
        raise HTTPException(422, f"PIN/密語至少 {MIN_LEN} 個字元（可用數字 PIN 或更長密語）")
    if len(pin) > MAX_LEN:
        raise HTTPException(422, f"PIN/密語過長（上限 {MAX_LEN} 字元）")
    low = pin.lower()
    if low in _COMMON_WEAK:
        raise HTTPException(422, "PIN/密語過於常見，請改用不易猜測的值")
    if len(set(pin)) == 1:
        raise HTTPException(422, "PIN/密語不可全為同一字元")
    if _is_run(pin):
        raise HTTPException(422, "PIN/密語不可為連續序列（如 123456）")
    if username and low == username.lower():
        raise HTTPException(422, "PIN/密語不可與帳號名相同")
