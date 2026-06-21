---
name: tak-device-cert-ca-topology
description: TAK 裝置證 CA 拓撲 — TAK 只信 ICS-TAK-SVC-CA（非 step-ca）；裝置證 offline 簽 + legacy/flat 包格式
metadata:
  node_type: memory
  type: project
  originSessionId: abe6851a-7cad-4d7d-b466-d2b98fc21fea
---

**雙 CA 刻意隔離（#305 / #315 真機實證）**：本部署有**兩個獨立 CA**，動 TAK 憑證前先認清是哪個：
- **step-ca**（daemon，`/ca-share` provisioner）= **ICS dashboard 登入證**（mTLS to ICS :443，`issue_p12` 線上發、後端不持鑰）。
- **ICS-TAK-SVC-CA**（offline，`tak-ca.key`+`tak-ca.pem` @ `/tak-certs/_ca`）= **TAK 一切**（TAK server cert + TAK client/device 證）。

🔑 **TAK truststore 只信 ICS-TAK-SVC-CA、不信 step-ca**。step-ca 簽的 client 證連 TAK → **`peer not verified`**（TAK messaging log `takserver-messaging.log` 實證）。所以 **TAK 裝置證（ATAK/iTAK）必須由 ICS-TAK-SVC-CA offline 簽**（後端讀 `TAK_DEVICE_CA_DIR=/tak-certs/_ca` 的 `tak-ca.key`，`-CAserial` 寫 workdir 因 CA dir 掛 ro）。`gen-device-pkg.sh`（`/tak-certs/_cascripts`）是 proven CLI；#315 `services/tak_device_cert.py` port 成 dashboard 線上發（`POST /api/admin/tak/device-cert`）。

**iTAK data package 格式（多次 reality check 才對，否則「匯入沒反應」或「Connection Failed」）**：
1. **legacy p12**（`openssl pkcs12 -export -legacy`，PBE+SHA1+RC2/3DES）—— iTAK/iOS 吃不下 openssl3 預設 PBES2/AES p12（同 [[ios-mtls-client-cert-packaging]] / #307 keychain 同生態限制）。
2. **flat zip**：`config.pref` + `<cn>.p12` + `truststore-root.p12` 在根，**無 MANIFEST、無 cert/ pref/ 子目錄**（gen-device-dp.sh 的嵌套/MANIFEST 結構 iTAK 不認）。pref 內 `cot_streams` 區塊放 connectString + `com.atakmap.app_preferences` 用 `clientPassword`。
3. **truststore = ICS-TAK-SVC-CA**（`tak-ca.pem`；裝置驗 TAK server 用，server cert 是它簽的）。
4. **connectString = 對外可達 TAK 位址**（公網/LAN IP `:8089:ssl`，**非**容器內網 `takserver:8089`）→ config `TAK_DEVICE_CONNECT_HOST`。
5. p12 密碼 = TAK 慣例 `atakatak`，內嵌進 pref → 裝置免打。
6. **8089 須對外 port-forward**（與 dashboard 443 不同 port，路由器要另開）。

成功訊號：TAK log `Added Subscription` + 裝置位置上 ICS COP 地圖。

**Why**：從「假設全用 step-ca」一路被真機/log 推翻到「TAK 自己的 CA + legacy + flat」花了數小時；不留痕下次必重踩。CA 隔離是 #305 刻意設計（ICS 登入證外洩也連不上 TAK，反之亦然）。

**How to apply**：動 TAK 裝置/憑證 = 用 ICS-TAK-SVC-CA（非 step-ca）；改 #315 包格式先比對 `gen-device-pkg.sh` + 使用者樣本；發證後驗 `takserver-messaging.log` 看 `Added Subscription` vs `peer not verified`。**TAK 裝置證後端確實用 offline CA 私鑰**（threat_model §8.3，非「後端不持鑰」——那只適用 ICS 登入證）。撤銷未做（裝置證無 app 層撤銷）。相關：[[tak-server-marti-cert-not-oauth]]、[[tak-marti-authz-model]]。
