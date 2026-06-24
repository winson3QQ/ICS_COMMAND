# GitHub Issue/PR Snapshot

此目錄為 **GitHub issue/PR 的結構化備份**，自動由
`.github/workflows/issue-snapshot.yml` 維護。

## 觸發
- `push` / `issues` / `issue_comment` / `pull_request*` event
- 每日 00:00 UTC schedule 兜底
- 手動 `workflow_dispatch`

## 用途
- C 方案 SoT 規則：GitHub = issue tracker SoT，Codeberg = code mirror
- 若 GitHub 帳號再被停權，可從這份 JSON 把 open issue 重建至 Codeberg
- 同時為人工稽核留下機讀備份（補強 ROADMAP《Compliance touchpoints》人讀紀錄；matrix.md 已廢）

## 復原流程
見 `docs/disaster/` 對應 runbook（停權後手動 import 至 Codeberg）。

## 不備份
- Issue / PR 內嵌的圖片（GitHub user-content URL）
- Reactions, project board associations, release artifacts
- Wiki, discussions
