# P1-12 統一 Key Management + At-rest 加密 + Backup GUI

> 本文從 `docs/ROADMAP.md` 抽出，為獨立設計規格文件。狀態以 ROADMAP.md 為準。

---

源於 P1-09 dogfood：當前 backup 機制（cron-style + env file 存 raw key）有 gap；加上 live DB at-rest 完全沒加密。**單獨修任一塊都會引入兩套 key management，所以統一設計**。

## 架構：分層 key derivation

```
FIDO2 token (CTAP2 hmac-secret extension)
     │  unlock at service start：PIN + touch
     ▼
master key (32 bytes，僅 process memory)
     │  HKDF-SHA256，label-based derive
     ▼
     ├── child[0] = "backup-v1"   → Fernet key（backup_db.py 用）
     ├── child[1] = "db-v1"       → SQLCipher key（live DB 加密）
     └── child[2..] = 未來用（audit log signing、session token 簽章）
```

- **單一 unlock 流程**：服務啟動時 prompt FIDO2 一次，所有 key 推導出來
- **單一 enroll 流程**：operator 一次註冊 2+ token，所有用途共享
- **單一復原 SOP**：rescue master key 紙本印出，所有層次都能還原
- **版本化 label**（`backup-v1` / `db-v1`）→ 未來 rotate 時新 label 對齊 migration window

## 三個 sub-item

### P1-12a：Key management 基礎建設（4-6 天）

- `scripts/keymgmt/enroll_fido2.py`：operator 插 token + PIN + touch，產 `/etc/ics/master-key.enc`（hmac-secret-wrapped）
- `scripts/keymgmt/unlock_key.py`：systemd ExecStartPre 呼叫，prompt FIDO2，解出 master，HKDF 推 child keys，export 環境變數給 service
- `scripts/keymgmt/derive_child.py` lib：標準 HKDF helper
- 多 token 冗餘：enroll N 把（主 / 備援 / 災後）任一把能 unlock
- Rescue：master key BIP-39-style 助記詞紙本輸出（可手動 reenroll）
- Fallback opt-in：env file mode 保留作 dev / no-FIDO2 場景，警示明顯
- 依賴：`python-fido2` (BSD, Yubico)、`libfido2` (BSD-2)、`cryptography` (Apache 2.0)。**全非中國**

### P1-12b：Backup / Restore 加密 + GUI（2-3 天）

**scope 從原本「backup DB」擴張到「整個 `data/` user-data 邊界」+ 三層觸發 + restore**（2026-05-28 修訂）。

P1-13 確立 `data/` = user-data 邊界後（CLAUDE.md 紅線），backup 應該涵蓋整個 `data/`（含 `ics.db` + `map_config.json` + 未來 user uploads / derived data），而不是 DB-only。

#### 三層 backup 觸發模型

| 層 | 觸發 | 目的 | 性質 |
|---|---|---|---|
| **L1 手動** | Admin 按「立即備份」按鈕 | 隨時 checkpoint（午休 / 換班 / 不安心 / debug 前） | user-driven，無語意 |
| **L2 演習結束** | `POST /api/exercises/{id}/archive` 自動觸發 | 「這個 backup = 演習 X 收尾完整狀態」 | 系統 ceremony，**有語意 + metadata** |
| **L3 防呆** | server shutdown lifespan / `POST /api/admin/reset-db` 之前自動觸發 | 不可逆操作前的最後一道保險 | 系統 enforce，**user 看不到但救得回來** |

#### 細項

- `backup_service.py` 擴張為 `user_data_backup_service.py`：tar `data/` 整包，排除 `data/backups/` 自己防遞迴
- `backup_db.py` CLI 保留作相容入口；新增 `backup_user_data.py` 主入口
- `BACKUP_KEY` env var（由 P1-12a unlock script 提供，HKDF child[0]）；移除舊 `BACKUP_ENCRYPTION_KEY` 路徑
- `POST /api/admin/backup`（admin role-only）：回 `{backup_file, sha256, size_mb, manifest}`
- L2 整合：archive 前先 backup → filename 含 exercise_id + name + ended_at → manifest 含 exercise metadata → UI 顯示「Backup 完成」+ 下載 + 可選「重置回出廠」
- **「重置回出廠（下場演習）」**：必須**同時**清 events + map_config + archive 當前演習（三件不可分，否則造成資訊孤兒）
- L3 hook：lifespan shutdown 攔 SIGTERM 跑 backup（best-effort，超時 30 s 放行）；`/api/admin/reset-db` 強制先 backup
- Admin panel：「立即備份」按鈕 + 進度動畫 + 下載；歷史 backup 列表（含 manifest 預覽）

#### Restore

```
[admin panel] → [備份管理]
  📤 還原備份
  [選擇檔案] backup-exercise-3-2026-05-15.tar.gz.enc
  ⚠ 將覆蓋當前資料。系統會先自動備份當前狀態為
     'pre-restore-{timestamp}.tar.gz.enc'
  [Manifest 預覽：exercise_id=3 / ended_at=2026-05-15 18:30 / 含 ics.db (3 MB) + map_config.json (8 KB)]
  [☐ 我了解，繼續還原]   [取消]
```

- `POST /api/admin/restore`（admin role-only）：上傳 tar.gz.enc + key unlock
- **自動 backup current 為 `pre-restore-{ts}`**（pre-flight 防呆）
- 解密 + 驗 manifest → 顯示給 user 確認 → 替換 `data/` + restart
- 限制：`status='active'` 的演習不允許 restore（需先 archive 或強制終止）
- 失敗情境：解密失敗 / manifest schema 不符 / disk 空間不足 → 不動 current data + 清 tmp + 回 errored response

#### 測試

- admin role 200 / operator role 403（backup + restore 雙路）
- backup 內容可解 + manifest 對得上
- migration（舊 backup 升 key 後仍能還原）
- L2：archive 觸發 backup，filename 帶 exercise metadata
- L3：reset-db 沒帶 force 必先 backup；SIGTERM 觸發 shutdown backup
- restore：pre-restore-{ts} 自動產生；active exercise 拒 restore（409）；解密失敗不動 current

#### 與未來 Wave 6 時間軸 replay 的分工

P1-12b 解的是「**結束時的完整快照**」+「**全或無 restore**」場景。Wave 6 才解「**演習中任一時間點**」+「**雙視窗對比**」場景（snapshot_repo 累積 COP entity state，UI 時間軸 scrub）。兩者不衝突；P1-12b 是 ops 紀律基本盤，Wave 6 是複盤金本位。

### P1-12c：Live DB at-rest 加密（SQLCipher）（4-7 天）

- 替換 `sqlite3` driver 為 `pysqlcipher3`（BSD-style，Zetetic 維護，**非中國**）
- 連線時 `PRAGMA key = "$DB_KEY";`（HKDF child[1]，從 P1-12a unlock 提供）
- Schema migration：既有明文 DB → 加密 DB 一次性轉換
- 風險評估：
  - SQLCipher 加密 overhead ~5-15%（讀寫），可接受
  - Pi 500 CPU 是否吃得消（CRYPTO benchmark needed）
  - 整 backup 鏈：明文 SQLite → SQLCipher 後 backup_db.py 需要拿 DB key 才能讀
- **不取代 LUKS 整碟加密**：SQLCipher 只保護 DB 檔；其他檔案（log / config / static）仍裸

## 依賴

- `python-fido2` (BSD, Yubico)、`libfido2` (BSD-2)
- `pysqlcipher3` (BSD-style, Zetetic)
- `cryptography` (Apache 2.0)
- Operator 採購 2+ FIDO2 token（YubiKey 5 / SoloKey / 任何 CTAP2 + hmac-secret）

**所有依賴非中國維護**（per CLAUDE.md 供應鏈規則）

## P1-12 整體 DoD

- [ ] **P1-12a**: FIDO2 enroll + unlock + HKDF 三 script 上線；多 token 冗餘可用；rescue 紙本 SOP 完成
- [ ] **P1-12b**: Backup 改用 derived key + 涵蓋整個 `data/`；三層觸發（L1 手動 / L2 演習結束 / L3 防呆）；Restore + 自動 pre-restore-{ts} 防呆；admin role-only GUI；migration script 過既有 backup
- [ ] **P1-12c**: SQLCipher live DB 加密；既有明文 DB migrate 完成；pytest 全綠（含 DB key inject）；Pi 500 效能 benchmark 不超 +20% latency
- [ ] **Unified key management 文件**：`docs/security/key-management.md` 含架構圖 + enroll SOP + rescue SOP + rotate SOP
- [ ] **Threat model 更新**：`docs/compliance/threat_model.md` 加 at-rest encryption 章節
- [ ] **失去所有 token 演練**：rescue 紙本可成功 reenroll 並解出歷史 backup + DB
- [ ] 測試覆蓋：mock FIDO2 device unit test + 真 token integration（CI 跑 mock，本機驗收真 token）

## Out of scope（明確不在 P1-12）

- **整碟 LUKS 加密**（Pi boot-time）→ 獨立 P1-NN，沿用 ICS_DMAS `initramfs/fido2-luks-hook` 移植但需 Pi 硬體實機驗證
- **Audit log signing key** / **Session token signing key**：架構已預埋（HKDF child[2..]），實際接線交給後續 item

## 工時總計

- P1-12a：4-6 天
- P1-12b：2-3 天
- P1-12c：4-7 天
- 文件 + threat model + 演練：1-2 天
- **總計**：10-17 天（不可並行做，依序）

## Bundling 決策（2026-05-28，dogfood 衍生）

P1-12b 與 P1-14 因架構重疊度高，建議**合併為單一 sub-phase「P1-12b+14：Exercise lifecycle data management」**（4-5 天，省 2 天 vs 序列做）。

| 重疊維度 | 影響 |
|---|---|
| 重置 workflow | P1-12b reset SOP 必須跟 P1-14 events scoping 同時設計，否則先做 P1-12b 用 `DELETE FROM events` 粗暴清，P1-14 來補時要 refactor |
| Backup manifest schema | P1-14 events 跟 exercise 綁定後 manifest 才能寫「這 backup 屬於 exercise X」；分兩次做 schema 改兩次 |
| Archive 行為 | P1-12b L2 archive backup 涵蓋 events 歸檔；P1-14 events FK 讓歸檔 query 乾淨；兩者天生綁定 |
| Admin GUI | 「演習管理」面板自然容納 backup 按鈕 + restore + 演習名稱 chip + 結束按鈕 |

排程：**P1-12a 之後 / P1-12c 之前**。Phase 1 內部建議順序：P1-10 全部完成 → P1-12a → **P1-12b+14 合併** → P1-12c。
