# ICS DMAS 測試目錄

> 目前共 **303 個測試案例**（**303 passed + 0 xfailed**），分四層執行（Unit / Integration / API / Security）。  
> 最新報告：cmd-v2.0.8，2026-04-25，303 passed（C3-D 第二階段新增 17 個 API 測試）  
> 注意：Linux/Mac CI 全 pass；Windows 環境下 1 個 test_writes_token_file 因 chmod 0o600 在 Win 無 Unix 語義而失敗（pre-existing，與功能無關）  
> 新增：C2-D `TestAdminPinLockout`（7）、C1-E `TestMigrationsTable`（6）+ `TestSchemaMigrationsApi`（4）、Admin PIN 503 測試（1）；**C3-D 階段一 `Test{CreateBackup,ListBackups,CleanupOldBackups,VerifyBackup,RestoreBackup}`（17）+ 階段二 admin API `Test{AdminPinGate,ListBackups,TriggerBackup,VerifyEndpoint,PreviewEndpoint,RestoreCmdEndpoint}`（17）**  
> Coverage：**44%**（legacy dead code `db.py` / `main_legacy.py` 於 2026-04-25 Session C audit 後移除）  
> 執行指令：`bash scripts/run_tests.sh`

---

## 測試分層說明

| 層 | 目的 | 特性 |
|----|------|------|
| **Unit** | 測單一函式的輸入輸出 | 不碰 DB、不起 server、最快 |
| **Integration** | 測模組之間的協作，包含真實 DB 操作 | 每個測試用獨立的暫存 SQLite |
| **API** | 模擬 HTTP 請求，測完整的請求→回應流程 | 透過 FastAPI TestClient，不需要起真正的 server |
| **Security** | 安全功能端對端驗證（C1-A+）| 鎖定邏輯、rate limit、first-run gate，部分需 DB + TestClient |

---

## Unit 測試（39 個）

### `test_helpers.py` — 基礎工具函式

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_correct_pin_verifies` | 用正確 PIN 驗證，應該通過 |
| `test_wrong_pin_fails` | 用錯誤 PIN 驗證，應該失敗 |
| `test_same_pin_different_salt` | 同一個 PIN 兩次加密，因為 salt 不同所以 hash 也不同（防彩虹表） |
| `test_deterministic_with_same_salt` | 固定 salt 下，同一 PIN 的 hash 結果一致（可重現驗證） |
| `test_empty_pin` | 空字串 PIN 也能正常加密與驗證，不會 crash |
| `test_returns_iso_format` | `now_utc()` 回傳的時間格式是 ISO 8601（含 T 和 Z） |
| `test_monotonic` | 連續呼叫 `now_utc()`，後者一定 ≥ 前者（時間不倒流） |
| `test_none_returns_none` | `iso_utc(None)` 安全地回傳 None，不 crash |
| `test_valid_iso_passthrough` | 已是正確格式的時間字串直接通過 |
| `test_invalid_returns_original` | 格式錯誤的字串不 crash，容錯處理 |
| `test_adds_positive_minutes` | `add_minutes("10:00", 30)` → "10:30" |
| `test_adds_zero_minutes` | 加 0 分鐘，時間不變 |
| `test_crosses_hour_boundary` | 10:45 + 20 分鐘 = 11:05（跨小時正確計算） |

---

### `test_calc_engine.py` — 計算引擎（規格 6.x）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_recent_is_ok` | 1 分鐘前的快照，新鮮度應為「ok」 |
| `test_warn_boundary` | 7 分鐘前的快照，應為「warn」或更嚴重 |
| `test_crit_boundary` | 16 分鐘前的快照，應為「crit」或「lkp」 |
| `test_lkp_very_old` | 30 分鐘前的快照，必定是「LKP（最後已知位置）」 |
| `test_minutes_field_is_positive` | 新鮮度結果的 `minutes` 欄位必須是正數 |
| `test_invalid_time_returns_lkp` | 時間格式錯誤時，安全地回傳 lkp，不 crash |
| `test_no_data_returns_zero` | 四個節點都沒資料時，MPI（醫療壓力指數）= 0 |
| `test_full_beds_raises_pressure` | 20 床占了 19 床且有 10 人等待，MPI 應 > 5（高壓） |
| `test_empty_beds_no_waiting` | 沒人用床也沒人等待，MPI = 0 |
| `test_components_present` | MPI 結果必須包含 `components` 分解欄位 |
| `test_forward_casualties_add_pressure` | 前進組 5 名紅傷患 × 0.8 = 4.0 壓力分 |
| `test_insufficient_data` | 沒有快照時，趨勢信心度為「insufficient」，rate 為 None |
| `test_upward_trend` | 10 分鐘內床位從 5 增到 15，方向應為「up」 |
| `test_flat_trend` | 10 分鐘內床位不變，方向應為「flat」 |
| `test_recent_minutes_ago` | `minutes_ago()` 對 5 分鐘前的時間應回傳 4.5～5.5 |
| `test_invalid_returns_999` | 格式錯誤時回傳 999.0，代表「未知/極舊」 |

---

### `test_auth_service.py` — Session 管理

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_returns_uuid_string` | `create_session()` 回傳 36 字元的 UUID 字串 |
| `test_session_stored` | 建立後，session 真的存在 in-memory store 裡 |
| `test_unique_tokens` | 兩次 `create_session()` 產生不同的 token |
| `test_valid_token` | 有效 token 可以取回 session 資料 |
| `test_invalid_token` | 不存在的 token 回傳 None |
| `test_expired_session` | 已過期的 session 被清除並回傳 None |
| `test_removes_session` | `destroy_session()` 真的把 session 從 store 移除 |
| `test_nonexistent_returns_none` | 對不存在的 token 呼叫 destroy 不 crash |
| `test_fresh_session_has_remaining` | 剛建立的 session，remaining 秒數 > 0 |
| `test_nonexistent_returns_zero` | 不存在的 token 的 remaining = 0 |

---

## Integration 測試（37 個）

> 每個測試使用獨立的 SQLite 暫存檔，測試結束自動刪除，互不影響。

### `test_account_repo.py` — 帳號管理

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_create_and_retrieve` | 建帳號後可以查到，欄位正確 |
| `test_duplicate_username_raises` | 重複的帳號名稱應拋出例外 |
| `test_pin_not_stored_in_plaintext` | DB 裡的 pin_hash 不是明文（安全性驗證） |
| `test_correct_credentials` | 正確帳密，`verify_login()` 回傳 `(account, "ok")` |
| `test_wrong_pin` | 錯誤 PIN，`verify_login()` 回傳 `(None, "bad_pin")` |
| `test_nonexistent_user` | 不存在的帳號，`verify_login()` 回傳 `(None, "no_user")` |
| `test_pin_not_in_response` | 登入成功的回傳值不包含 `pin_hash`、`pin_salt`（不洩漏機敏資料） |
| `test_creates_admin_if_empty` | 第一次啟動，自動建立 admin 帳號 |
| `test_idempotent` | 呼叫兩次 `ensure_default_admin()` 不會建出兩個 admin |
| `test_default_pin_works` | 預設 admin 帳號的 PIN 是「1234」，可以登入，`reason="ok"` |
| `test_suspend_and_login_fails` | 停用帳號後，即使密碼正確也回 `(None, "suspended")` |
| `test_successful_login_resets_failed_count` | 連續 3 次錯誤 PIN 後，正確登入 → `failed_login_count=0`、`locked_until=None`（lockout reset 驗證）|

---

### `test_exercise_repo.py` — 演練場次

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_basic_create` | 建演練後，id 存在，status 為「setup」 |
| `test_defaults` | 新建演練的 mutex_locked=0、ended_at=null |
| `test_get_exercise` | 用 id 可以查到正確的演練 |
| `test_list_exercises` | 建兩個演練後，列表長度 ≥ 2 |
| `test_activate_sets_status` | 啟動後 status=active、mutex_locked=1、started_at 有值 |
| `test_only_one_active_allowed` | 已有一個 active，再啟動第二個應拋出例外（mutex） |
| `test_get_active_exercise` | `get_active_exercise()` 回傳正確的進行中演練 |
| `test_archive_releases_mutex` | 歸檔後 status=archived、mutex_locked=0、ended_at 有值 |
| `test_can_activate_new_after_archive` | 舊演練歸檔後，可以啟動新的（mutex 釋放） |

---

### `test_event_repo.py` — 事件管理

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_creates_with_code` | 建事件後，event_code 以「EV-」開頭 |
| `test_code_sequential` | 第二個事件的序號大於第一個（遞增不重複） |
| `test_status_defaults_to_open` | 新建事件的 status 預設為「open」 |
| `test_with_exercise_id` | 指定 exercise_id 的事件，用該 ID 查詢時查得到 |
| `test_open_to_in_progress` | 事件可以從「open」轉為「in_progress」 |
| `test_in_progress_to_resolved` | 事件可以從「in_progress」轉為「resolved」 |
| `test_open_to_resolved_directly` | 事件可以從「open」直接轉為「resolved」 |
| `test_filter_by_status` | `get_events(status=...)` 只回傳指定狀態的事件 |
| `test_note_appended` | 新增筆記後，事件的 notes 欄位包含該筆記內容 |

---

### `test_decision_repo.py` — 決策管理

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_basic_create` | 建決策後，status 預設為「pending」 |
| `test_with_primary_event` | 決策可以關聯到特定事件，查詢時 primary_event_id 正確 |
| `test_approve_decision` | 指揮官核准後，status=approved、decided_by 有值 |
| `test_hold_decision` | 指揮官暫緩後，status=hold |
| `test_nonexistent_raises` | 對不存在的決策執行 decide，應拋出例外 |
| `test_list_decisions` | 建兩個決策後，列表長度 ≥ 2 |
| `test_filter_by_exercise` | 只回傳屬於特定演練的決策 |

---

## API 測試（64 個）

> 透過 FastAPI TestClient，模擬完整的 HTTP 請求→認證→邏輯→回應流程。

### `test_auth_api.py` — 認證流程

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_success` | 正確帳密，HTTP 200，回傳 session_id |
| `test_wrong_pin` | 錯誤 PIN，HTTP 401 |
| `test_nonexistent_user` | 不存在的帳號，HTTP 401 |
| `test_missing_fields_422` | 缺少 pin 欄位，HTTP 422（格式錯誤） |
| `test_no_token_returns_401` | 沒帶 X-Session-Token，HTTP 401 |
| `test_invalid_token_returns_401` | 帶假的 token，HTTP 401 |
| `test_valid_token_passes` | 帶有效 token，HTTP 200 |
| `test_returns_remaining` | Heartbeat 回傳 remaining 秒數 > 0 |
| `test_without_token_401` | Heartbeat 沒帶 token，HTTP 401 |
| `test_returns_user_info` | `/api/auth/me` 回傳 username、role，不含 pin_hash |
| `test_logout_invalidates_session` | Logout 後，同一 token 再請求應收到 401 |

---

### `test_events_api.py` — 事件管理 API

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_success` | 正確欄位建事件，回傳 event_code |
| `test_invalid_severity_422` | severity 填「high」（不合法），HTTP 422 |
| `test_invalid_unit_422` | reported_by_unit 填未知單位，HTTP 422 |
| `test_missing_operator_name_422` | 缺少 operator_name，HTTP 422 |
| `test_with_exercise_id` | 帶 exercise_id 建事件，HTTP 200 |
| `test_empty` | 還沒建事件時，列表回傳空陣列 |
| `test_after_create` | 建事件後，列表長度 ≥ 1 |
| `test_filter_by_status` | `?status=open` 只回傳 open 的事件 |
| `test_in_progress` | 把事件狀態改為 in_progress，HTTP 200 |
| `test_resolve` | 經由 in_progress 轉到 resolved，HTTP 200 |
| `test_nonexistent_event_returns_4xx` | 對不存在的事件改狀態，HTTP 4xx |
| `test_add_note` | 對事件新增筆記，HTTP 200 |
| `test_create_then_list_contains_exact_event_id` | 建事件後 list 中可用精確 ID 找到，`event_code` 一致 |
| `test_patch_nonexistent_event_status_returns_4xx` | PATCH 不存在事件的 status → 4xx（ValueError→HTTP 400）|
| `test_add_note_to_nonexistent_event_returns_404` | POST notes 到不存在事件 → 404（router 明確轉換 ValueError→404）|

---

### `test_exercises_api.py` — 演練管理 API

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_empty_list` | 還沒建演練時，列表為空陣列 |
| `test_after_create` | 建演練後，列表長度為 1 |
| `test_minimal_fields` | 只帶 name + type，HTTP 200，status 為 setup |
| `test_with_all_fields` | 所有欄位都帶，location 欄位正確儲存 |
| `test_missing_name_422` | 缺少 name，HTTP 422 |
| `test_activate` | 啟動演練，HTTP 200，status 變 active |
| `test_mutex_second_activate_fails` | 已有 active 演練，再啟動第二個應 HTTP 409 |
| `test_nonexistent_exercise_404` | 啟動不存在的演練 ID，HTTP 404 |
| `test_archive_after_active` | 歸檔已啟動的演練，HTTP 200，status 變 archived |
| `test_can_activate_after_archive` | 歸檔後可以啟動新演練，HTTP 200 |
| `test_create_aar` | 建 AAR 紀錄，HTTP 200，有 id |
| `test_list_aar` | 建兩筆 AAR 後，列表長度為 2 |
| `test_create_then_list_contains_exact_id` | 建演練後 list 中可用精確 ID 找到，status 為 setup |
| `test_second_activate_returns_exactly_409` | 第二個 activate（mutex 衝突）→ 精確 409，不是 400 |
| `test_archive_nonexistent_exercise_returns_404` | archive 不存在的演練 ID → 404（router 存在性守門）|

---

### `test_pi_push_api.py` — Pi 節點推送 API（13 個）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_shelter_heartbeat` | shelter 空推送（心跳），HTTP 200，ok=true |
| `test_medical_heartbeat` | medical 空推送（心跳），HTTP 200 |
| `test_no_bearer_returns_401` | 沒帶 Authorization header，HTTP 401 |
| `test_wrong_token_returns_403` | 錯誤的 Bearer token，HTTP 403 |
| `test_unknown_unit_returns_403` | 未知 unit_id，HTTP 403 |
| `test_cross_unit_token_rejected` | shelter token 推送 medical，HTTP 403（跨節點不可用） |
| `test_push_returns_ok` | shelter 推 persons + beds + resources，HTTP 200，records_count 正確 |
| `test_push_stores_batch` | 推送後 `/api/pi-data/shelter/list` 回傳正確筆數 |
| `test_push_groups_by_table` | grouped 欄位依 table_name 分組，persons/beds 各 2 筆 |
| `test_push_returns_ok` (medical) | medical 推 patients + resources + incidents，HTTP 200 |
| `test_push_stores_batch` (medical) | 推送後 `/api/pi-data/medical/list` offline=false |
| `test_offline_before_push` | 推送前查詢 offline=true |
| `test_two_nodes_push_independently` | shelter + medical 各自推送，資料不互相干擾 |

---

### `test_dashboard_api.py` — Dashboard 聚合驗證（10 個）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_returns_200` | `/api/dashboard` HTTP 200 |
| `test_pi_nodes_empty_before_registration` | 未建節點時 pi_nodes 為空陣列 |
| `test_no_auth_returns_401` | 未帶 token，HTTP 401 |
| `test_pi_node_appears_after_push` | shelter 推送後 pi_nodes 列表出現 shelter |
| `test_shelter_bed_used` | 2 人已安置 → shelter_history[0].bed_used = 2 |
| `test_shelter_pending_intake` | 1 人等候中 → shelter_history[0].pending_intake = 1 |
| `test_medical_red_casualties` | 1 位紅傷患在場 → medical_history[0].casualties_red = 1 |
| `test_medical_excludes_discharged` | 已離區傷患不計入 bed_used（3 人中 1 已離，bed_used=2） |
| `test_both_nodes_in_pi_nodes_list` | shelter + medical 同時推送，pi_nodes 包含兩者 |
| `test_both_histories_populated` | 兩節點推送後，shelter_history 和 medical_history 都有資料 |

---

---

## Security 測試（19 個）—— C1-A Phase 1（cmd-v2.0.2）

> 對應規格：`docs/commercialization_plan_v1.md §C1-A`  
> 測試檔路徑：`tests/security/`

### `test_login_lockout.py` — 登入鎖定 + IP rate limit（9 個）

#### `TestAccountLockout`（5 個，DB 層）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_fifth_wrong_pin_locks_account` | 第 5 次錯誤 PIN → `reason="locked"`，帳號鎖定 15 分鐘 |
| `test_locked_account_rejects_correct_pin` | 鎖定中即使輸入正確 PIN 也回 `"locked"`（防鎖定期暴力） |
| `test_successful_login_resets_failed_count` | 成功登入後 `failed_login_count=0`、`locked_until=NULL` |
| `test_unlock_account_clears_lock` | `unlock_account()` 管理員手動解鎖，之後可正常登入 |
| `test_lockout_expires_after_duration` | 直接改 DB `locked_until` 到過去時間，模擬 15 分鐘已過，應可登入 |

#### `TestLoginAPILockout`（2 個，HTTP 層）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_locked_returns_423` | 帳號鎖定後 `POST /api/auth/login` 回 HTTP 423 Locked |
| `test_no_user_returns_401_not_404` | 不存在帳號回 401，不洩漏帳號是否存在 |

#### `TestIPRateLimit`（2 個，HTTP 層）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_eleventh_request_returns_429` | 同一 IP 第 11 次 login 回 HTTP 429，且帶 `Retry-After` header |
| `test_rate_limit_only_applies_to_login` | 非 `/api/auth/login` 路徑（如 `/api/health`）不受 rate limit |

---

### `test_first_run.py` — 首次強制設定流程（10 個）

#### `TestEnsureInitialAdminToken`（5 個，DB 層）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_creates_admin_with_random_pin` | 空 DB 首次啟動，自動建立 admin，PIN 為 6 位數字 |
| `test_writes_token_file` | PIN 寫入 `first_run_token` 檔案，權限 0600 |
| `test_returns_none_if_accounts_exist` | 已有帳號時回傳 `None`（冪等，不重複建） |
| `test_pin_unpredictable` | 連續 5 次獨立執行 PIN 不重複（隨機性 sanity check） |
| `test_initial_pin_is_default_flagged` | 新建 admin 的 `is_default_pin=1`，`is_first_run_required()=True` |

#### `TestClearDefaultPinFlag`（1 個，DB 層）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_clear_after_pin_change` | `clear_default_pin_flag()` 後 `is_first_run_required()=False` |

#### `TestFirstRunGate`（4 個，HTTP 層）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_login_allowed_when_first_run` | 白名單：`POST /api/auth/login` 在 first-run 狀態仍可通過，回傳 `must_change_pin=true` |
| `test_health_allowed_when_first_run` | 白名單：`GET /api/health` 不被 gate 擋 |
| `test_other_apis_blocked_with_423` | 非白名單路徑（即使持有有效 token）回 HTTP 423，body `code="FIRST_RUN_REQUIRED"` |
| `test_change_pin_clears_gate` | 改 PIN 後 `is_default_pin=0`，gate 解除，原本被擋的 API 恢復正常 |

---

---

## Security 測試擴充（51 個）—— Smoke v2（2026-04-24）

> 對應規格：韌性 / 安全 / 模糊測試  
> 測試檔路徑：`tests/security/`

### `test_sync_integrity.py` — 同步完整性（9 個）

#### `TestWithinPayloadDuplicate`（1 個）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_duplicate_snapshot_id_in_single_sync_payload_last_wins` | 單一 sync payload 內兩筆相同 snapshot_id → Three-Pass 逐筆串行：第一筆 INSERT，第二筆 UPDATE（last wins=99），DB 只有 1 筆 |

#### `TestReplayDuplicateSync`（5 個）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_first_insert_returns_inserted_true` | 首次推送 `inserted=True` |
| `test_duplicate_returns_inserted_false` | 相同 snapshot_id 二次推送 → `inserted=False` |
| `test_duplicate_does_not_increase_db_count` | 重複推送不增加 DB 筆數（恰好 1 筆）|
| `test_duplicate_with_modified_data_ignored` | 相同 ID 不同資料 → 原始資料保留（INSERT OR IGNORE）|
| `test_duplicate_via_api_both_return_200` | 透過 API 重複推送：兩次都 200，不拋錯 |

#### `TestOutOfOrderSync`（3 個）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_latest_reflects_snapshot_time_not_arrival` | 排序依 `snapshot_time`，亂序不影響 latest |
| `test_old_snapshot_does_not_displace_latest` | 舊時間戳快照推入後，最新快照不被取代 |
| `test_future_timestamp_accepted` | 未來時間戳（時鐘偏移）系統接受，不拋錯 |

---

### `test_session_edge.py` — Session 超時邊界（10 個）

#### `TestSessionStrictBoundary`（2 個）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_heartbeat_api_remaining_refreshes` | heartbeat 後 `remaining` 不繼續遞減（API 層 remaining 欄位驗證）|
| `test_expired_session_heartbeat_returns_401` | 過期 session 呼叫 `/api/auth/heartbeat` → 401（HTTP 層，不只是 check_and_touch）|

#### `TestSessionTimeout`（4 個）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_session_valid_just_before_timeout` | `SESSION_TIMEOUT - 1s`：session 仍有效 |
| `test_session_expired_just_over_timeout` | `SESSION_TIMEOUT + 1s`：session 過期，回 None |
| `test_expired_session_deleted_from_db` | 過期觸發時，token 從 DB 實際刪除 |
| `test_session_remaining_zero_after_expiry` | 過期後 `session_remaining()` 回傳 0 |

#### `TestSessionHeartbeat`（1 個）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_heartbeat_via_api_extends_session` | `GET /api/auth/heartbeat` 刷新 `last_active` |

#### `TestSessionDestroy`（3 個）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_logout_invalidates_token` | 登出後原 token 打 `/api/auth/me` → 401 |
| `test_same_user_can_have_multiple_sessions` | 同帳號可同時持有多個 session（多裝置）|
| `test_logout_one_session_does_not_affect_other` | 登出一個 session 不影響同帳號其他 session |

---

### `test_concurrent.py` — 並發 / Mutex 競爭（5 個，含 1 xfailed）

#### `TestExerciseMutexSerial`（2 個）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_second_activate_raises` | 序列呼叫：第二個 activate 拋 `ValueError` |
| `test_archive_releases_mutex` | 歸檔釋放 mutex，可再啟動新演練 |

#### `TestExerciseMutexConcurrent`（1 個，xfailed）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_concurrent_activate_at_most_one_wins` | ⚠️ **TOCTOU 風險**：並發 activate 若出現兩個 active → `xfail`（已知風險，C2 修正）|

#### `TestConcurrentSnapshotPush`（2 個）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_two_threads_push_different_snapshots` | 兩執行緒推不同 snapshot_id → 兩筆都進 DB |
| `test_two_threads_push_same_snapshot_id` | 兩執行緒推相同 snapshot_id → DB 只有 1 筆，不崩潰 |

---

### `test_auth_bypass.py` — 管理員繞過嘗試（15 個）

#### `TestAdminPinProtection`（5 個）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_list_accounts_without_pin_returns_403` | 無 X-Admin-PIN → 403 |
| `test_list_accounts_wrong_pin_returns_403` | 錯誤 PIN → 403 |
| `test_list_accounts_empty_pin_returns_403` | 空字串 PIN → 403 |
| `test_create_account_without_pin_returns_403` | POST /api/admin/accounts 無 PIN → 403 |
| `test_delete_account_without_pin_returns_403` | DELETE /api/admin/accounts/{u} 無 PIN → 403 |

#### `TestPiPushProtection`（2 個）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_pi_push_without_bearer_returns_401` | 無 Authorization header → 401 |
| `test_pi_push_wrong_token_returns_403` | 錯誤 Bearer token → 403 |

#### `TestTokenForging`（5 個）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_nonexistent_token_returns_401` | 不存在的 UUID token → 401 |
| `test_sql_injection_in_token_returns_401` | SQL 注入字串作 token → 401，不崩潰 |
| `test_very_long_token_returns_401` | 10,000 字元 token → 401，不崩潰 |
| `test_empty_token_returns_401` | 空字串 token → 401 |
| `test_no_token_returns_401` | 完全不帶 header → 401 |

#### `TestOpenEndpointsByDesign`（3 個，設計決策文件）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_snapshot_post_is_open_by_design` | POST /api/snapshots 無 session 200（機對機設計，非漏洞）|
| `test_snapshot_get_is_open_by_design` | GET /api/snapshots/{type} 無 session 200（Pi 讀取，設計）|
| `test_health_is_open` | GET /api/health 無 session 200（監控探針）|

---

### `test_payload_fuzzing.py` — 惡意/畸形 Payload（15 個）

#### `TestLoginFuzzing`（6 個）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_integer_pin_coerced_or_422` | pin 傳整數 → 強制轉換或 422，不崩潰 |
| `test_sql_injection_in_username` | SQL 注入 username → 401，DB 不受影響 |
| `test_sql_injection_in_pin` | SQL 注入 pin → 401，不崩潰 |
| `test_missing_username_returns_422` | 缺 username → 422 |
| `test_missing_pin_returns_422` | 缺 pin → 422 |
| `test_empty_body_returns_422` | 空 body → 422 |

#### `TestEventFuzzing`（5 個）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_invalid_severity_returns_422` | 非法 severity 值 → 422 |
| `test_xss_in_description_stored_safely` | XSS payload 原樣存入（後端不 escape，前端責任）|
| `test_sql_injection_in_description_stored_safely` | SQL 注入存入 → DB 不受影響，events 表仍存在 |
| `test_oversized_description` | 50,000 字元 description → 200 或 422，不崩潰 |
| `test_unicode_and_emoji_in_description` | Unicode + emoji → 正常處理 |

#### `TestSnapshotFuzzing`（4 個）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_missing_snapshot_id_returns_422` | 缺 snapshot_id → 422 |
| `test_unknown_type_returns_400` | 未知 node type → 400 |
| `test_extra_unknown_fields_accepted` | 額外未知欄位（`extra="allow"`）→ 200 |
| `test_negative_bed_values_accepted` | 負數 bed_used → 接受（已知行為，C2 加 validator）|

---

---

## Security 測試擴充（38 個）—— Smoke v3（2026-04-24）

> 測試檔路徑：`tests/security/`

### `test_admin_gate.py` — Admin 權限集中化守門（10 個）

#### `TestSessionVsAdminPin`（4 個）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_valid_session_without_admin_pin_returns_403` | 有效 session + 無 X-Admin-PIN → 403（session 不替代 PIN）|
| `test_valid_session_with_wrong_admin_pin_returns_403` | 有效 session + 錯誤 PIN → 403 |
| `test_no_session_with_correct_admin_pin_passes` | 無 session + 正確 PIN → 200（admin 端點豁免 session auth）|
| `test_valid_session_with_correct_admin_pin_passes` | 有效 session + 正確 PIN → 200 |

#### `TestRoleVsAdminPin`（3 個）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_operator_without_admin_pin_gets_403` | 操作員角色 + 無 PIN → 403（角色不替代 PIN）|
| `test_operator_with_correct_admin_pin_can_access` | 操作員 + 正確 PIN → 200（PIN 才是守門關鍵）|
| `test_operator_cannot_escalate_own_role_without_pin` | 操作員嘗試升級自己角色（無 PIN）→ 403 |

#### `TestAdminPinBoundary`（3 個）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_admin_pin_not_setup_verify_returns_false` | 空 DB 無 admin_pin → `verify_admin_pin()` 回 False |
| `test_admin_pin_change_takes_effect_immediately` | 改 PIN 後舊 PIN 立刻失效，新 PIN 立刻生效 |
| `test_delete_nonexistent_account_returns_404` | 刪除不存在帳號（正確 PIN）→ 404 |

---

### `test_sync_extended.py` — /api/sync/push 三 Pass 同步（9 個）

#### `TestSyncEmptyPayload`（2 個）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_empty_sync_push_returns_200` | 空 payload → 200，不崩潰 |
| `test_empty_sync_does_not_change_db` | 空 payload 不增加任何 snapshots |

#### `TestSyncIdempotency`（3 個）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_same_payload_twice_idempotent` | 相同 payload 推兩次 → 快照數不變（Pass 1 UPDATE）|
| `test_sync_pass1_updates_existing_snapshot` | 已存在 snapshot_id → Pass 1 UPDATE（不同於 INSERT OR IGNORE）|
| `test_sync_result_consistent_on_retry` | 失敗重試 → 結果與第一次一致 |

#### `TestSyncOutOfOrder`（2 個）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_old_timestamp_via_sync_does_not_displace_latest` | 透過 sync push 的舊時間戳不取代最新快照 |
| `test_mixed_timestamps_all_stored` | 亂序多筆快照全部存入 DB（不丟棄）|

#### `TestSyncUnknownUnit`（2 個）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_unknown_source_unit_accepted` | 未知 source_unit → 200（容錯設計）|
| `test_sync_without_snapshots_key` | 缺 snapshots 欄位 → 200 或 422，不崩潰 |

---

### `test_session_config.py` — Session 設定邊界（6 個）

#### `TestSessionTimeoutConfig`（2 個）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_custom_short_timeout_expires_session` | SESSION_TIMEOUT=2s：2 秒後 session 過期 |
| `test_zero_timeout_immediately_expires` | SESSION_TIMEOUT=0：session 立即過期（邊界值）|

#### `TestCleanupExpiredSessions`（2 個）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_cleanup_removes_expired_sessions` | `cleanup_expired_sessions()` 移除所有過期 session |
| `test_cleanup_returns_zero_when_nothing_expired` | 無過期 session 時 cleanup 回傳 0 |

#### `TestConcurrentSession`（2 個）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_concurrent_requests_with_same_token` | 同 token 10 條並發請求 → 全部 200（不競爭損壞）|
| `test_session_touch_updates_last_active` | `check_and_touch` 每次都更新 `last_active`（非 read-only）|

---

### `test_firstrun_config.py` — First-run / Secure-mode 配置邊界（13 個）

#### `TestCSPModeSwitch`（3 個）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_report_only_mode_uses_correct_header_name` | CSP_MODE='report-only' → header 名稱為 `Content-Security-Policy-Report-Only` |
| `test_enforce_mode_uses_correct_header_name` | CSP_MODE='enforce' → header 名稱為 `Content-Security-Policy` |
| `test_csp_report_only_does_not_block_requests` | report-only 模式不阻擋正常請求 |

#### `TestSecurityHeadersToggle`（2 個）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_csp_header_present_by_default` | 預設 CSP header 存在（FastAPI 層）|
| `test_security_headers_absent_when_disabled` | ENABLE_SECURITY_HEADERS=false → CSP header 不送出 |

#### `TestAllowedOriginsParsing`（4 個）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_default_origins_not_empty` | 預設 ALLOWED_ORIGINS 包含 localhost |
| `test_origins_strip_whitespace` | 多餘空白被去除 |
| `test_empty_origins_env_yields_empty_list` | ALLOWED_ORIGINS='' → 空 list（CORS 全拒）|
| `test_single_origin_parsed_correctly` | 單一 origin 解析為長度 1 的 list |

#### `TestFirstRunGateBoundary`（4 個）

| 測試案例 | 驗證什麼 |
|---------|---------|
| `test_is_first_run_false_when_no_accounts` | 空 DB 無帳號 → `is_first_run_required()` 回 False（非「無帳號」觸發）|
| `test_is_first_run_true_only_when_default_pin_flagged` | `is_default_pin=1` 才觸發，普通帳號不觸發 |
| `test_first_run_token_file_idempotent` | token 檔冪等性：空 DB 重建不崩潰 |
| `test_gate_allows_whitelisted_paths_when_active` | gate 啟動時白名單路徑（/api/status）仍通過 |

---

## 目前未覆蓋的範圍

| 模組 | 覆蓋率 | 原因 | 預計在 |
|------|--------|------|--------|
| `repositories/sync_repo.py` | 9% | 三 Pass 同步邏輯需要完整情境 | C2 |
| `repositories/manual_repo.py` | 19% | 人工紀錄 CRUD | C2 |
| `repositories/ttx_repo.py` | 22% | TTX 場景注入流程複雜 | C2 |
| `routers/ttx.py` | 25% | 同上 | C2 |
| `routers/map.py` | 29% | 需要實際的 MBTiles 檔案 | C2 |
| `routers/admin.py` | 37% | 大量帳號管理 CRUD | C2 |
| `services/ai_service.py` | 36% | AI stub，C5-C 才實裝 | C5-C |
| `calc_engine.py` | 61% | 下半段為 Wave 6 功能，尚未實裝 | Wave 6 |
| ~~`db.py` / `main_legacy.py`~~ | — | **已於 2026-04-25 Session C audit 後刪除** | ✅ |

> **已關閉的風險項**  
> ✅ ~~演練 mutex TOCTOU~~：2026-04-25 hotfix（commit 待補）— `exercise_repo.update_exercise_status` 改原子寫入（`UPDATE ... WHERE NOT EXISTS(...)` 單 statement + rowcount 檢查）；`test_concurrent_activate_at_most_one_wins` 從 xfail 改為 passed。

---

## 如何新增測試

1. 在對應的層（unit/integration/api）建立新的 `test_XXX.py`
2. 在測試檔開頭加 `pytestmark = pytest.mark.unit`（或 integration/api）
3. 執行 `bash scripts/run_tests.sh unit` 確認通過
4. 完整跑 `bash scripts/run_tests.sh` 確保沒有回歸
