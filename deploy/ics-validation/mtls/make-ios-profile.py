#!/usr/bin/env python3
"""make-ios-profile.py — #275：把 root CA + 裝置 p12 包成 iOS .mobileconfig 設定描述檔。

解決交付痛點：現場人員在 iPhone 看到裸 .crt/.p12 + 「尚未簽署描述檔」，不知在裝什麼。
.mobileconfig 有清楚的名稱/說明/單位/用途，且可內嵌 p12 密碼（免打、免被自動大寫卡）。
一次安裝同時設好「信任根 CA」+「本裝置 mTLS 身分憑證」。

用法：
  python3 make-ios-profile.py <CN> <p12-path> <p12-password> <root-ca-pem> <server-url> [out.mobileconfig]

例：
  python3 make-ios-profile.py my-phone out/my-phone/my-phone-num.p12 147147 \
      out/my-phone/root_ca.crt https://1.34.230.218/ out/my-phone/my-phone.mobileconfig
"""
import base64
import ssl
import sys
import uuid
from xml.sax.saxutils import escape


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def main() -> None:
    if len(sys.argv) < 6:
        print(__doc__)
        sys.exit(1)
    cn, p12_path, p12_pass, root_path, server_url = sys.argv[1:6]
    out = sys.argv[6] if len(sys.argv) > 6 else f"{cn}.mobileconfig"

    root_der = ssl.PEM_cert_to_DER_cert(open(root_path, encoding="ascii").read())
    p12 = open(p12_path, "rb").read()

    ca_uuid, id_uuid, top_uuid = (str(uuid.uuid4()).upper() for _ in range(3))
    cn_x = escape(cn)
    url_x = escape(server_url)

    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>PayloadContent</key>
  <array>
    <dict>
      <key>PayloadType</key><string>com.apple.security.root</string>
      <key>PayloadVersion</key><integer>1</integer>
      <key>PayloadIdentifier</key><string>local.ics.ca.{ca_uuid}</string>
      <key>PayloadUUID</key><string>{ca_uuid}</string>
      <key>PayloadDisplayName</key><string>ICS 指揮部 根憑證 (CA)</string>
      <key>PayloadDescription</key><string>信任 ICS 內部憑證簽發機構，使本裝置能驗證指揮伺服器並出示裝置憑證。</string>
      <key>PayloadCertificateFileName</key><string>ics-root-ca.crt</string>
      <key>PayloadContent</key>
      <data>{_b64(root_der)}</data>
    </dict>
    <dict>
      <key>PayloadType</key><string>com.apple.security.pkcs12</string>
      <key>PayloadVersion</key><integer>1</integer>
      <key>PayloadIdentifier</key><string>local.ics.identity.{id_uuid}</string>
      <key>PayloadUUID</key><string>{id_uuid}</string>
      <key>PayloadDisplayName</key><string>ICS 裝置憑證 — {cn_x}</string>
      <key>PayloadDescription</key><string>本裝置登入 ICS 指揮儀表板的 mTLS 身分憑證（第二因子）。</string>
      <key>PayloadCertificateFileName</key><string>{cn_x}.p12</string>
      <key>Password</key><string>{escape(p12_pass)}</string>
      <key>PayloadContent</key>
      <data>{_b64(p12)}</data>
    </dict>
  </array>
  <key>PayloadType</key><string>Configuration</string>
  <key>PayloadVersion</key><integer>1</integer>
  <key>PayloadIdentifier</key><string>local.ics.enroll.{top_uuid}</string>
  <key>PayloadUUID</key><string>{top_uuid}</string>
  <key>PayloadOrganization</key><string>ICS 指揮部</string>
  <key>PayloadDisplayName</key><string>ICS 指揮部 裝置接入 — {cn_x}</string>
  <key>PayloadDescription</key><string>安裝後本裝置即可從 {url_x} 登入 ICS 指揮儀表板（仍需登入 PIN）。內含：信任根憑證 + 本裝置 mTLS 身分憑證（{cn_x}）。遺失裝置請通知管理員撤銷此憑證。</string>
</dict>
</plist>
"""
    with open(out, "w", encoding="utf-8") as f:
        f.write(plist)
    print(f"✓ {out}（裝置 {cn}；安裝畫面顯示名稱/說明，p12 密碼已內嵌免打）")


if __name__ == "__main__":
    main()
