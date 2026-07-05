# ICS_Command Runtime 黑箱驗證 Log — #301（2026-07-05）

> 授權安全驗證（系統擁有者明確要求）。收口 [#301](https://github.com/winson3QQ/ICS_COMMAND/issues/301)：
> 2026-06-20/21 白箱審查殘留「只有 runtime 才驗得到」的 4 項，對 **live prod** 實測。
> 標的：prod `ics-command:dev`（backend 2.36.1 / frontend v1.27.2），nginx 1.27.5 mTLS @ :443。
> 規則：每項附實測證據；標 **事實 / 推論**。

## 方法與限制（事實）
- prod = Windows/Docker（`ics-prod-*` 容器）；host 即 prod SoT。
- **mTLS `ssl_verify_client on`**：無 client cert 的請求在 nginx 即 **400**（app 前擋下）。
  → 純外部黑箱只能驗「握手/邊界層」；打 app 的活靶（IDOR/traversal）需持證 session，故 item 4
  以「跑 prod image 同一份防禦邏輯的安全測試 + 對 live app 無證探測守門」佐證（見下）。

## 查核結果

### ① 安全 headers（#289 H4）— ✅ PASS（黑箱實測）
`curl -skI https://127.0.0.1/`（無證 → 400，但 `add_header ... always` 於錯誤回應仍帶）實得：
```
Strict-Transport-Security: max-age=31536000; includeSubDomains
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: camera=(), microphone=(), geolocation=()
```
4 項要求 header 皆在（+ Permissions-Policy 加碼）。源：`deploy/.../default.conf:47-51`。

### ② TLS 版本 / cipher（#289 H4）— ✅ PASS
`openssl s_client` 實際協商 **TLSv1.3 + TLS_AES_256_GCM_SHA384**（AEAD）。
config：`ssl_protocols TLSv1.2 TLSv1.3`（無 1.0/1.1）；`ssl_ciphers` 全 ECDHE + GCM/CHACHA20
（AEAD，無 CBC）；`ssl_prefer_server_ciphers on`。**推論**：全 offered 套件為強套件（未跑 nmap 全枚舉，
但 config 已限定 + 協商結果強）。

### ③ prod secret（#290 M1）— ✅ PASS
`ICS_PROXY_SHARED_SECRET` 於 prod 容器 **SET、len=48**（真隨機 hex，非預設非空）；nginx 帶對應
`X-Proxy-Auth`（`default.conf`）→ 內網直打後端偽造 cert header 之路被堵（#280 修補生效）。

### ④ IDOR / traversal / upload 活靶（#286 / #288）— ✅ PASS（app-layer + live 守門）
- **測試**（prod image 同一份 code）：`test_map_upload_security.py`（#286 traversal）+
  `test_event_decision_write_scope.py` + `test_exercise_scoping.py`（#288 跨場 IDOR）+
  `test_auth_bypass.py` = **44 passed**。
- **live 無證探測**：對運行實例 `:8000` 打 `/api/events`、`/api/decisions`、
  `/api/exercises/1/tracks`、`/api/cop/entities` → 全 **401**（守門在跑）。
- **推論**：因 mTLS，#286/#288 之攻擊面收斂為「持證者/內部」，非公網任何人；持證 session 的
  完整外部活靶重演屬紅隊另案（非本單阻擋項）。

## 結論
**4 項全數驗畢通過**（①②③黑箱直驗；④ app-layer 測試 44 pass + live 401 守門）。#301 收口。
持證 session 的端到端外部活靶（穿 mTLS 打 app）列為紅隊持證另跑，非本單完成前提。
