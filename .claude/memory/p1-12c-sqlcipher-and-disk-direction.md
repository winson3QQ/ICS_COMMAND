---
name: p1-12c-sqlcipher-and-disk-direction
description: 12c SQLCipher 已 merge 但 default-off（L2 內層、非 at-rest 主控）；整碟
metadata:
  node_type: memory
  type: project
  originSessionId: fe8cf4ef-35db-471f-b5a0-2fec1bc3a528
---

P1-12c（SQLCipher live DB at-rest 加密）2026-06-21 完成並 merge（PR #304 / merge commit `fd413ea`，[#229](https://github.com/winson3QQ/ICS_COMMAND/issues/229) CLOSED）。但**預設關閉**（`ICS_DB_ENCRYPTED` 未設 → 原生 sqlite3）。

**策略定位（threat_model §8.4，別重新爭論）**：SQLCipher = **L2 內層縱深，不是 at-rest 主控**。同機部署下 TAK PostgreSQL 同碟明文 + 私鑰明文 → 偷碟直接讀 TAK PG 繞過 SQLCipher。**完整 at-rest = 整碟加密（[#231](https://github.com/winson3QQ/ICS_COMMAND/issues/231) LUKS/BitLocker）才是 L1 主控**。是否真開 12c 內層待整碟策略定。

**驗證事實**：Windows 無 `sqlcipher3` wheel（已實測）→ 裸 Windows 跑不了加密路徑，只能明文。加密邏輯由 **CI ubuntu**（= prod 的 Linux 容器環境）驗，`tests/integration/test_db_encryption.py` 真 SQLCipher round-trip，CI 已綠。動 12c 加密路徑時別期待本機能驗。

**交付方向（[#231](https://github.com/winson3QQ/ICS_COMMAND/issues/231) 前置，2026-06-21 user 拍板）= 預封碟（pre-sealed disk + FIDO2 token）**：
- 「堅持整碟加密」≠「只能交付整機」。逼到整機的是更高需求 B（硬體來源/防拆/evil-maid/TPM 綁定）——**user 暫不納 B**。
- 預封碟 = vendor 封好 LUKS 碟 + token，客戶提供機殼。與 12a 的 `disk-v1`（**FIDO2-LUKS 可攜、綁 token，非 TPM 綁機器**）天生對口；維護比整機輕。**此分叉可回頭**（威脅升級再升整機）。
- **下一步未決**：子題 1「封整碟（含 OS root）vs 只 app+資料 payload 碟」尚未答 —— 這決定後面所有做法。驗證可先用 USB SSD 預封碟 prototype（Linux VM，不卡硬體）；開機 root FIDO2 解鎖（initramfs）+ Pi 效能 = 硬體尾巴 [#230](https://github.com/winson3QQ/ICS_COMMAND/issues/230)。

umbrella [#226](https://github.com/winson3QQ/ICS_COMMAND/issues/226) 仍 OPEN（剩 #230 硬體 + #231 整碟 + 12c 是否真開）。相關 [[deployment-topology-windows-docker]]（prod=Windows 跑 Docker，驗證在容器/CI 不在裸機）。
