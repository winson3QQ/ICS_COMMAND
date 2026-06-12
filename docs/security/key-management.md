# 統一 Key Management（P1-12a，#227）

> **狀態**：12a 基建落地（mock 驗證）；真 token 整合驗收 + 失 token 演練 → [#230](https://github.com/winson3QQ/ICS_COMMAND/issues/230)。
> 設計規格：[`docs/roadmap/p1-key-management.md`](../roadmap/p1-key-management.md)。部署 at-rest 策略拍板：[`threat_model.md` §8.4](../compliance/threat_model.md)（2026-06-12，v0.6）。

## 架構

```
FIDO2 token（CTAP2 hmac-secret；enroll N≥2 把，任一把可解）
     │ unlock：PIN + touch
     ▼
master key（32 bytes，僅存 process memory；紙本助記詞為唯一離線副本）
     │ HKDF-SHA256（info = "ics-keymgmt/v1/" + label）
     ▼
     ├── backup-v1 → BACKUP_KEY（Fernet，backup 加密，P1-12b #228 消費）
     ├── db-v1     → DB_KEY（hex，SQLCipher PRAGMA key，P1-12c #229 消費）
     ├── audit-v1  → 預留（audit log 簽章，P1-12 之後接線）
     └── disk-v1   → LUKS unlock（§8.4 統一託管，#231 消費；不寫入 app env file）
```

- **單一 unlock**：一次 FIDO2 解出 master，所有 child 推導而出
- **單一 rescue**：master 助記詞紙本，所有層次都能還原
- **rotate**：換新 label（如 `backup-v2`）對齊 migration window，master 不動

## 檔案與工具

| 檔 | 內容 | 安全性質 |
|---|---|---|
| `/etc/ics/master-key.enc` | per-token AES-256-GCM wrap 的 master（JSON）| 無 token 時是廢紙；竄改 → GCM 認證失敗報錯 |
| `/run/ics/keys.env` | unlock 後的 child keys（0600，tmpfs）| **明文金鑰材料**；重開機即消失；勿入版控 |
| 紙本助記詞 | master 本體（BIP-39 24 字）| 離線保險箱；遺失 + token 全失 = 資料永久不可解 |

| Script | 用途 |
|---|---|
| `scripts/keymgmt/enroll_fido2.py` | 註冊 token / 印 rescue 助記詞 / `--from-mnemonic` 災後 re-enroll |
| `scripts/keymgmt/unlock_key.py` | 解鎖 → 衍生 → 寫 env file（systemd `EnvironmentFile=-/run/ics/keys.env` 消費）|
| `scripts/keymgmt/derive_child.py` | HKDF 衍生 lib（其他模組 import，單一收口）|

## SOP

### Enroll（初次部署，operator 一次性）

```bash
python scripts/keymgmt/enroll_fido2.py --store /etc/ics/master-key.enc --tokens 2 --show-rescue
# 依提示逐把「插入 → PIN → touch」；助記詞抄紙本 → 離線保險箱 → 清螢幕
```

### 日常啟動（manned C2，§8.4 拍板形態）

```bash
python scripts/keymgmt/unlock_key.py --store /etc/ics/master-key.enc --output /run/ics/keys.env
sudo systemctl start ics-command
```

### Rescue（失去所有 token）

```bash
# 1. 先以助記詞臨時解鎖讓服務能起
python scripts/keymgmt/unlock_key.py --from-mnemonic --output /run/ics/keys.env
# 2. 新 token 到手後立即 re-enroll（master 不變 → 歷史 backup / DB 全部仍可解）
python scripts/keymgmt/enroll_fido2.py --store /etc/ics/master-key.enc --tokens 2 --from-mnemonic
```

### Dev / CI fallback（無 FIDO2 場景，警示明顯）

```bash
ICS_MASTER_KEY=$(python -c "import secrets;print(secrets.token_bytes(32).hex())")
ICS_MASTER_KEY=$ICS_MASTER_KEY python scripts/keymgmt/unlock_key.py --output .keys.env
```

**production 禁用 fallback** — master 以明文 env 存在即繞過整個 FIDO2 層。

## 安全邊界（勿誤解）

- **只防 at-rest**（偷碟 / 備份外洩 / 關機複製）。runtime 已解密、master 在記憶體 — 主機特權存取讀得到一切（threat_model §3.4 / §7）。
- **SQLCipher（12c）≠ 偷碟免疫**：同機 TAK PostgreSQL 仍明文 — **LUKS（#231）為主控必備**，本套件的 `disk-v1` child 是它的 unlock 預留位（§8.4）。
- Windows 上 python-fido2 直走 CTAP HID 需 Administrator；真 token 操作建議 Mac/Linux（#230）。
