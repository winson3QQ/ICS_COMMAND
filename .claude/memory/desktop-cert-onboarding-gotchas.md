---
name: desktop-cert-onboarding-gotchas
description: ICS 登入 mTLS 憑證跨平台上手坑（桌機=.p12+root CA、iOS=.mobileconfig；Mac/Chrome/cert-bound session 踩雷點）
metadata:
  node_type: memory
  type: project
  originSessionId: c80f5a97-6a4f-4a8d-8a87-5d48d93a7712
---

ICS **登入用 mTLS client 憑證**（進 dashboard 443，step-ca 簽，**與 TAK 裝置證是兩套不同 CA**，見 [[tak-device-cert-ca-topology]]）的跨平台交付/上手坑。2026-06-22 真機 iMac dogfood 實證（#327）。

**格式對應平台**（面板「發憑證」fmt）：
- **桌機 Windows / iMac / Android = `.p12`**（client 證；發證自動把 CN 綁到該帳號）。
- **iOS = `.mobileconfig`**（描述檔，密碼內嵌免打）。
- **桌機要連得進來還需 root CA 信任 server** → 面板「**下載 root CA**」鈕（#327：`GET /api/admin/ca/root`，sysadmin，回 step-ca root PEM 公開證）。p12 本身**不附** root CA。

**五個踩過的雷**：
1. **別用 iOS 描述檔裝桌機** → Mac 會一直跳「Safari 想存取鑰匙圈密碼 **Configuration Profiles**」（profile 存的私鑰每次用都要授權；按「永遠允許」可止）+ root 不受信任。桌機一律 .p12。
2. **macOS root CA 匯入「登入」鑰匙圈，勿丟「系統根」**（System Roots 唯讀、改不了 → 跳「無法修改系統根」）。匯入後雙擊 → 「信任」→ 「永遠信任」。Windows 走「受信任的根憑證授權單位」；Android 走 設定→安全→安裝憑證→「CA 憑證」（.p12 走「VPN 與 App 使用者憑證」）。
3. **cert-bound session（#275）**：client 證的 **CN 必須綁到登入帳號**，否則 mTLS 在 nginx 過了（頁面載得出）、但登入後 API 立刻 401 → 前端「**閒置過久，請重新登入**」（console 一堆 `session_expired_to_login`）。症狀＝頁面進得去卻一直被踢。
4. **Chrome（Mac）會記住對該站選過的 client 證**：換證後若仍帶舊的（沒綁帳號那張）→ 一直被踢；**完全結束 Chrome（⌘Q）重開**才會重新跳「選擇憑證」，選對的（有綁帳號那張）即可。
5. iOS 第三方瀏覽器（Chrome/Firefox=WebKit 殼）拿不到 client 證 → iOS 須用原生 Safari（threat_model §8.6）。

**判斷流程**：頁面有「憑證無效」警告＝root CA 沒信任（坑 2）；頁面進得去但一直「閒置過久」＝client 證沒綁帳號 or Chrome 帶錯證（坑 3/4）。`backend-v2.13.0`/`frontend-v1.9.3`。
