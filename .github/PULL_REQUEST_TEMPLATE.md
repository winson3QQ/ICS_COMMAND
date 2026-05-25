<!--
ICS_Command PR template — 對應 docs/PROCESS.md 步驟 4
§8 Human Verification Script 必填，Human ② verify 時直接跑
-->

## ROADMAP item

<!-- 例：P1-10a — 採用 WaveInk Design System -->
- Closes #<issue-number>

## What changed

<!-- 1-3 行說明 -->

## DoD checklist

<!-- 對照 ROADMAP 該 item 的 DoD；勾掉已達成的 -->
- [ ] Tests added / updated
- [ ] `pytest` green
- [ ] `npm test` green（若有 JS 改動）
- [ ] 規格書更新（若有介面 / 資料格式 / 行為變更）
- [ ] ROADMAP item 對應 sub-item 完成

## §8 Human Verification Script

<!--
**CA 必填，IA 必跑過一遍**。Human ② verify 直接跑下列步驟。
任一步 fail → PR comment 留 `VERIFY-FAIL: <step> <現象>`，回退到 CA。
全 pass → PR comment 留 `VERIFY-PASS`。
-->

```bash
# Step 1: <描述>
<指令>
# 預期：<expected output / 行為>

# Step 2: <描述>
<指令>
# 預期：<expected output / 行為>
```

## Out of scope / known limitations

<!-- 明確列出本 PR 不做什麼，避免 reviewer 期待落差 -->

## Notes for reviewer

<!-- 給 /code-review 或 /security-review 的 hint，例如「特別注意 cop_service schema 變更」 -->
