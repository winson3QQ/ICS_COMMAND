---
name: tak-aware-datapackage
description: TAK Aware iOS data package 生成方式 — step-ca 簽 client cert，不能用 TAK 內建 CA
metadata:
  node_type: memory
  type: project
  originSessionId: 9827f209-06fb-4a74-9eb1-28beaa8e28ce
---

TAK Aware iOS 連 TAK Server 要用 step-ca 簽的 client cert data package，不能用 :8446 cert enrollment 生的。

**Why:** TAK Server 的 `truststore-root.jks` 已換成 step-ca（root + intermediate）。:8446 enrollment 簽的 client cert 是 TAK 內建 CA，server 不認 → `PEER_DID_NOT_RETURN_A_CERTIFICATE` → TAK Aware 顯示 "Cancelled For Server"。

iTAK 連得上是因為它的 data package 剛好用了 step-ca 簽的 cert；TAK Aware 的 data package 來源不同。

**症狀識別：**
- TAK Aware status: "Waiting For Server" → "Cancelled For Server"
- server log: `PEER_DID_NOT_RETURN_A_CERTIFICATE` on port 8089
- TAK Aware 設定頁 Server Certificate Expiration 若顯示 **2036 年**= TAK 內建 CA，不是 step-ca

**生成正確 data package 的流程（step-ca daemon 不需要跑）：**

```bash
CALLSIGN="3QQ-AWARE"  # 改成目標 callsign
P12_PASS="atakatak"
CERT_DIR="/tmp/step-client-$CALLSIGN"
INT_CA="$HOME/.step/certs/intermediate_ca.crt"
ROOT_CA="$HOME/.step/certs/root_ca.crt"
INT_KEY="$HOME/.step/secrets/intermediate_ca_key"
PASS_FILE="$HOME/.step/secrets/password"

mkdir -p "$CERT_DIR"

# 1. 簽 client cert（offline）
step certificate create "$CALLSIGN" \
  "$CERT_DIR/client.crt" "$CERT_DIR/client.key" \
  --ca "$INT_CA" --ca-key "$INT_KEY" \
  --ca-password-file "$PASS_FILE" \
  --not-after="2160h" --no-password --insecure --force
cat "$CERT_DIR/client.crt" "$INT_CA" > "$CERT_DIR/client-fullchain.crt"

# 2. 轉 PKCS12
openssl pkcs12 -export \
  -in "$CERT_DIR/client-fullchain.crt" -inkey "$CERT_DIR/client.key" \
  -name "$CALLSIGN" -out "$CERT_DIR/${CALLSIGN}.p12" -passout "pass:$P12_PASS"

# 3. truststore p12（root + intermediate）
keytool -importcert -noprompt -alias step-root \
  -file "$ROOT_CA" -keystore "$CERT_DIR/truststore-root.p12" \
  -storetype PKCS12 -storepass "$P12_PASS"
keytool -importcert -noprompt -alias step-intermediate \
  -file "$INT_CA" -keystore "$CERT_DIR/truststore-root.p12" \
  -storetype PKCS12 -storepass "$P12_PASS"
```

**zip 結構：**
```
MANIFEST/manifest.xml
cert/<CALLSIGN>.p12
cert/truststore-root.p12
pref/config.pref  （connectString0=172.20.10.2:8089:ssl）
```

**How to apply:** 新裝置要接 TAK Server 時，一律用這個流程，不用 :8446 enrollment。AirDrop 到裝置後 TAK Aware 自動識別並匯入；舊連線要先 Delete Connection。

---

## [2026-06-16 dogfood 重大修正 — 別再從憑證開始查]

**先驗檔案能不能被讀，再驗憑證。** 這次「import 顯示 processed successfully 但 Connected Servers 空的」整整查了一輪憑證/CA/佈局，最後 device debug log 才證明 **zip 根本沒被讀進去**：
- log 關鍵：`[TAKDataPackageImporter]: Unable to copy file ...zip to package cache: Code=260 "no such file"` → `Package files empty!` → **卻仍 `show alert Data package processed successfully!`**（TAK Aware **1.8.1.262** 的 bug：不管成不成都報成功，極度誤導）。
- 根因：選檔來源是 **雲端 File Provider 佔位符**（路徑含 `File Provider Storage/item|1|...!s...`，OneDrive 那類），檔案沒下載到本機 → security-scoped 拿不到實體檔。
- **修法：先讓 zip 變本機實體檔再匯入** — AirDrop → 儲存到「我的 iPhone」(On My iPhone)，或在檔案 App 先點下載；別從雲端資料夾直接選。

**版本落差陷阱：** 使用者裝的是 **TAK Aware 1.8.1**，但 public repo（github.com/flighttactics/TAKAware）main 與所有 tag 都停在 **v1.5 / 2025-06**，且 1.8 的多連線 UI（"Add a connection" / "No Connected Servers" / "Sit(X) OAuth"）字串在 public source 完全不存在 → **桌面/上游 source 不能拿來推 1.8 的 import 行為**。1.8 的「Upload a Data Package」走 `TAKDataPackageImporter`（非舊版的 `TAKDataPackageParser`）。

**Server 實測狀態（推翻本檔上半部「truststore 純 step-ca」前提）：** 2026-06-16 live `172.20.10.2:8089` 實測：
- server 出示的身分憑證 = **ICS_DMAS Dev CA**（CN=tak.ics.local，keystore `takserver.jks`，Jun 15 產），**不是** step-ca。
- 但 `truststore-root.jks` 仍只信任 **step-ca**（root+intermediate）。→ **keystore 與 truststore 用不同 CA = 半遷移不一致狀態。**
- 實測 server **接受 ICS_DMAS Dev CA 簽的 client cert**（TLS1.2 handshake 成功）；用桌面那個 ICS_DMAS 包做完整 mTLS `Verify return code: 0 (ok)` → **包的憑證對 server 是好的**。
- 反而 `gen-device-dp.sh` 的 step-ca 包會因 server 身分是 ICS_DMAS 而被 client truststore 拒。**動 client 接入前先確認 server keystore/truststore 兩邊 CA 是否一致。**

**`gen-device-dp.sh` aware 模式三個 bug（對 v1.5 parser 而言；commit 0371a1e）：** ①寫 `certificatePassword` 但 parser 只認 `clientPassword` ②cert 放 `cert/` 子目錄但 v1.5 parser 用 basename+空 rootFolder 找不到 ③用 step-ca 簽與現行 ICS_DMAS server 身分衝突。（1.8 行為未知，勿照 v1.5 結論硬套。）

---

## [2026-06-16 續：判定 TAK Aware 1.8.1「Upload a Data Package」是 app 缺陷 — 不存 client identity]

**結論（已用 4 版包證實，package-independent）：** TAK Aware **1.8.1.262** 的 Add a connection → Upload a Data Package（走 `TAKDataPackageImporter`）會：登記 server(`Registering server`)、存 server truststore(`Storing cert chain with 3 cert(s)`)、但**從不把 client identity 寫進 keychain**。連線時 `[SettingsStore]: Identity was not stored in the keychain -25300`(errSecItemNotFound)→ 不出示 client cert → status Failed。整份 log **從來沒有 "Storing User Certificate"/"Adding Identity"/"[PKCS12]" 任何一行**（對照 server cert 每次都有）。

**已排除的包變因（4 版桌面 zip 全部相同 -25300，故都不是因）：** ①密碼 key 名稱(同放 `clientPassword`+`certificatePassword`) ②佈局(flat / `cert/` 子夾) ③p12 加密(RC2-40 → `-descert` 3DES) ④identity bag(fullchain → leaf-only)。client p12 = RSA2048 / iOS 相容。

**已實證 server+包+網路全部 OK：** 用包內 ICS_DMAS client cert + truststore 對 live `172.20.10.2:8089` 跑完整 mTLS = `Verify return code: 0 (ok)`；server TLS1.2 接受該 client cert；檔案改 AirDrop/本機開後 importer 讀得到(雲端佔位符那關已過)。

**決定的下一步（使用者選）= 改走 Certificate Enrollment**（Add a connection → Certificate Enrollment，帳號/密碼 CSR 到 :8446，走另一條會正確存 identity 的 `CSRRequestor` 路徑）。**未開工的前置（下次接手做）：**
1. **收斂 server CA 不一致**（根本病灶）：現況 keystore=`takserver.jks`=**ICS_DMAS Dev CA**(CN=tak.ics.local) / `truststore-root.jks`=**step-ca**。要讓 :8446 enrollment 簽出的 cert 被 :8089 接受 → enrollment 的 `<certificateSigning>` CA 必須在 :8089 truststore 內。先查 `CoreConfig.xml` 的 `<certificateSigning>`/`<auth>`、`/opt/tak/certs/files/` 有哪組 CA 私鑰可簽，再決定全收斂成 ICS_DMAS 或 step-ca（單一方向）。
2. **開 TAK user 帳號**（enrollment 要帳密）：`UserAuthenticationFile.xml` 或 `/opt/tak/utils/UserManager.jar`。
3. 驗 :8446 enrollment 端到端 → TAK Aware 連上。
（查證指令起點：`docker exec takserver bash -lc 'cat /opt/tak/CoreConfig.xml'`、`ls /opt/tak/certs/files`、`keytool -list`。takv 證據在 `~/Downloads/OneDrive_1_2026-6-16/TAKAware-Debug*.log`。桌面測試包 `~/Desktop/3QQ-AWARE-canon{,2,3,4}.zip`。）

[[tak-server-marti-cert-not-oauth]]
