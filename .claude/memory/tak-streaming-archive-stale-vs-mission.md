# TAK streaming = 原生 stale+archive；可靠刪除只在 Mission/DataSync

## 事實（真機 iTAK + 活 server dogfood 實證，2026-06-08，issue #161）
- **iTAK「從地圖刪除」是純本機 declutter**：刪 marker/繪圖 → :8089 wire 零 `t-x-d-d`、TAK server（Marti `GET /Marti/api/cot/xml/{uid}`）原封不動。
  新增/編輯**會**上 wire + 入 server；**只有刪除不傳播**。
- **TAK server 持久化一切**：CoreConfig `<repository enable=true>`（PostgreSQL）存每個 uid 最新 CoT，**不靠 stale 移除**；`<latestSA enable=true>` 新連線補發；`<repeater>` 僅 4 種 emergency 重播（一般 marker 不重播）。
- **持久訊號 = CoT `<archive/>`**（不是 `how`）：帶 `<archive/>`（如 `a-u-G`/`a-h-G` 放置標記）→ client 過 stale 也保留；無 archive（如繪圖 `u-d-r`）→ 原生 client 過 stale 即移除（server repository 仍留）。
- **`stale` = client 顯示提示**，非 server 刪除條件。
- Mission 層才有完整 CRUD 同步：`GET /Marti/api/missions`（dogfood 時為空 `[]`，故那些 marker 全是裸 streaming、無權威刪除）。Mission 內刪除 → server 廣播給訂閱者 → 真正「刪了大家都刪」。

## 決策（#161 scope，取代 WIP commit 0ba8fde 的 last-heard time 窗口）
- **ICS = 一般 streaming subscriber，顯示對齊原生 = honor `stale` + honor `<archive/>`**。退掉 `COP_STALE_REMOVE_WINDOW_S` time 窗口。
- archived entity 持久（豁免 stale）、non-archived 過 stale 移除。
- **「mission 與否」不是消除條件**：archived 非-mission marker 仍要留（原生）。mission 來源標記 + 權威刪除留 P2-14。

## How to apply
- `parse_cot_xml`（`services/tak_service.py`）萃取 `<archive/>` → CoPEntity `archived` 旗標（DB 欄位，似 P2-11b planned/simulated，小 migration）。
- `list_cop_entities`（`repositories/cop_entity_repo.py`）：external TAK `archived=1` → 豁免 stale；否則依 `stale` 過期。
- 前端 `map.js` `_isAging`：archived 不變灰；non-archived 過 stale 才灰→移除（live 移除免 refresh）。
- **可靠刪除傳播 / 權威 resync → P2-14**（接 Mission/DataSync Marti，沿用 [[tak-server-marti-cert-not-oauth]] 的 cert）；過渡期操作員「移出 COP」+ 無界成長安全網（P2-14(A)）。
- mission/streaming 圖層 filter（純視圖）→ P2-25。
- 威脅模型：「streaming 層刪除不同步」為 COP 完整性結構性限制 → `docs/compliance/threat_model.md`（P2-17）。
- 相關：[[tak-server-marti-cert-not-oauth]]、[[exercise-scope-server-authoritative]]。
