# 演習/實戰歸屬 = server-authoritative（不信 client 宣告）

## Doctrine（核心架構決策）
CoP entity 的「演習 / 實戰 / 兩者皆非」歸屬,由 **指揮部 server 端決定**,**不信任 client（ATAK 裝置）宣告**:
- `exercise_id` 由 `current_exercise_id()`（指揮部 UI 當前 active exercise）決定:
  有 active → 綁該演習場;無 active → NULL 池（實戰 / 未分場）。
- 模式（演習 vs 實戰）= 指揮部 UI 開哪個場,**連入的 ATAK 裝置就歸屬那個模式**。
- **TAK Server 與指揮部後端獨立啟停**（只開 TAK 沒指揮部 → 無 ingest、CoT 不進 cop_entities；
  只開指揮部沒 TAK → 無 CoT、歸屬純 server）。三種組合（只 TAK / 只指揮部 / 都開）**無一需要信
  ATAK opex** → ATAK 端無從、也不該決定「演習 vs 實戰」（使用者 2026-06-07 確認場景）。
- 對齊既有明文 doctrine:[[tak-server-marti-cert-not-oauth]] 旁的 `resolve_scope`
  註解（exercise_service.py）+ `routers/cop.py` POST 強制 `source='manual'`——
  「範圍由 server 端 active 狀態 + 角色決定,不讓 client 自行宣告」。

## TAK-C 紅隊建議：評估後不採（2026-06-07，使用者拍板）
紅隊 TAK-C 提議讓 `normalize_cot` 讀 ATAK 的 `opex`（o/e/s）來決定 exercise 歸屬
（`opex="o"` → 強制 NULL 池等）。**不採**,因為:
- 讓 client 宣告（opex）覆寫 server 模式 = 給每個 ATAK 裝置「自宣告繞過演習場」開關,
  **違反不信任 client 宣告 doctrine**,反成新完整性破口。
- `simulated` 同理:應由 **P2-19 O/C 注入路徑（server 端）** 設,**非**信 ATAK `opex="s"`。
- 紅隊原始擔憂（演習中混入實戰 CoT、reset 清掉實戰資料）的真正解 =
  **演習/實戰部署隔離**（指揮部不同時跑兩模式 / 獨立 instance），非信 client opex。
- 使用者確認：**無「同一指揮部 instance 同時混演習+實戰」需求** → 維持 `current_exercise_id()` 現狀,不動 code。

## How to apply
- 任何「這筆資料屬於哪場 / 是否演習」的判斷,一律走 server 端（`current_exercise_id()` /
  `resolve_scope`）,**不讀 client 送來的 opex / source / exercise_id 宣告**。
- 未來若紅隊 / ROADMAP 再提「讀 opex 分流」,記得這條 = 已評估不採。
- `simulated` 標記只在 server 端注入路徑（P2-19）設,TAK ingest 維持 default False。
