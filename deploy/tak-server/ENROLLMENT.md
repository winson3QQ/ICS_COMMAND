# TAK Certificate Enrollment 設定（:8446 signClient）— #411 / #420 Phase B

讓場端裝置（TAK Aware / iTAK / ATAK）以 **帳密 + CSR 向 :8446 自助 enroll** 取得 client cert，
取代 offline 簽 + data package。**為何**：TAK Aware 1.8.1 的「Upload a Data Package」是 app 缺陷
（`Identity was not stored in the keychain -25300`，不存 client identity → 連線失敗，換幾個包都沒用，
2026-06-27 二度實證）；enrollment 走 `CSRRequestor` 路徑才正確存證。iTAK/ATAK 仍可用 data package
（dashboard `POST /api/admin/tak/device-cert`，`tak_device_cert.py` 已 `-legacy`）。

> ⚠ **安全前提（不可顛倒）**：enrollment 需把 **`ICS-TAK-SVC-CA` 私鑰上線進 TAK**（簽證庫）。
> 動此設定**前**務必先做 **VPN 前置 + 公網 cutover**（[`../perimeter/wireguard/README.md`](../perimeter/wireguard/README.md)），
> 確認 TAK `:8089`/`:8446` 對公網零入口（外部實測不可達）**才**上 CA。threat_model §8.3（2026-06-27 段）。

## 一次性設定（live：`release/tak/`，皆 gitignored runtime，故記於本檔）

### 1. 簽證庫 `signing-ca.jks`（= ICS-TAK-SVC-CA，含私鑰）
簽出的 enrolled cert 須被 `:8089` truststore（`truststore-root.jks` = ICS-TAK-SVC-CA）信任，故簽證 CA
**必須**是同一把。從 offline CA（`<prod>/tak-certs/_ca/`）做：
```bash
CA=<prod>/tak-certs/_ca           # tak-ca.pem + tak-ca.key
FILES=release/tak/certs/files
# host openssl 做 p12（免 keytool）
openssl pkcs12 -export -in "$CA/tak-ca.pem" -inkey "$CA/tak-ca.key" \
  -name ics-tak-svc-ca -out "$FILES/signing-ca.p12" -passout pass:atakatak
# 容器內 keytool p12→jks（host 無 JDK）
docker exec takserver sh -c 'cd /opt/tak/certs/files && \
  keytool -importkeystore -noprompt -srckeystore signing-ca.p12 -srcstoretype PKCS12 \
  -srcstorepass atakatak -destkeystore signing-ca.jks -deststoretype JKS -deststorepass atakatak'
rm -f "$FILES/signing-ca.p12"   # p12 即用即刪（CA 私鑰只留 _ca）
```
> ⚠ MSYS（Git Bash on Windows）對 `docker exec ... /opt/...` 會把容器路徑改成 `C:/Program Files/Git/opt/...`
> → 前綴 `MSYS_NO_PATHCONV=1`。

### 2. CoreConfig.xml 加 `<certificateSigning>`（`<Configuration>` 子節點，置 `<security>` 前）
語法照 `/opt/tak/CoreConfig.example.xml`（別猜）：
```xml
<certificateSigning CA="TAKServer">
    <certificateConfig>
        <nameEntries>
            <nameEntry name="O" value="ICS"/>
            <nameEntry name="OU" value="COP"/>
        </nameEntries>
    </certificateConfig>
    <TAKServerCAConfig keystore="JKS" keystoreFile="/opt/tak/certs/files/signing-ca.jks"
        keystorePass="atakatak" validityDays="365" signatureAlg="SHA256WithRSA"/>
</certificateSigning>
```
改完 `docker restart takserver`（api 層約 90s 才綁 :8443/:8446）。

### 3. enrollment 帳號（帳密）
```bash
MSYS_NO_PATHCONV=1 docker exec takserver \
  java -jar /opt/tak/utils/UserManager.jar usermod -p '<密碼>' <username>
```
- **密碼複雜度（UserManager 強制）**：≥15 字元 + 大寫 + 小寫 + 數字 + 特殊符（`-_!@#$%^&*…`）。不合格 → 靜默不生效。
- enrolled cert 的 CN = `<username>`；走 :8089 後落 `__ANON__`（未做 #403 門禁前），群分配靠 #344。

## 三個會卡死的坑（實機踩過）

| 症狀 | 根因 | 解 |
|---|---|---|
| GET `/Marti/api/tls/config` 401 | 手寫 UserAuthenticationFile 的 `passwordHashed="false"` 明文密碼 TAK 不收；usermod 去**更新**它會 `IllegalArgumentException: Invalid salt`（把明文當 bcrypt） | **別手寫**；`usermod -D <user>` 刪掉壞的，再 `usermod -p` 全新建（passwordHashed→true） |
| POST `signClient` 500 `CSR validation failed!` | CSR subject 漏 nameEntries（只給 CN） | CSR DN 須 `O=ICS/OU=COP/CN=<user>`。TAK Aware 先 GET config 拿 nameEntries 再建故合格；手測 curl 要自己補 O/OU |
| signClient 200 但裝置仍 Failed | TAK Aware data-package 缺陷（非 enrollment） | 用 **Certificate Enrollment**（非 Upload a Data Package） |

## 自測（server 端驗，免動手機）
```bash
# 1) config 應 200 回 nameEntries
curl -sk -u '<user>:<pw>' https://127.0.0.1:8446/Marti/api/tls/config
# 2) 帶正確 DN 的 CSR 應 200 回憑證
openssl req -new -newkey rsa:2048 -nodes -keyout t.key -out t.csr -subj "/O=ICS/OU=COP/CN=<user>"
curl -sk -u '<user>:<pw>' -H "Content-Type: text/plain" --data-binary @t.csr \
  "https://127.0.0.1:8446/Marti/api/tls/signClient/v2?clientUid=selftest&version=1.8"
```
> 自測會在 TAK `certificate` 帳本留一筆（client_uid=selftest），驗完用 `DELETE FROM certificate WHERE client_uid='selftest'` 清掉。

## 裝置端（TAK Aware）
Add a connection → **Certificate Enrollment** → Host=`<WG IP，如 10.13.13.1>`、Username/Password、
Cert Enroll Port=`8446`、Port=`8089`；裝置須先連 VPN。

## 相關
- `deploy/tak-server/pki/gen-device-dp.sh` 為 **step-ca** 簽的舊腳本，**TAK truststore 不信 step-ca**
  → superseded（用 dashboard 線上發證或本 enrollment）。
- doctrine：threat_model §8.3（CA 上線取捨）、memory `tak-enrollment-working`、#403（門禁 Phase C，未做）。
