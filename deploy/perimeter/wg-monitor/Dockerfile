# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
#
# #447 獨立 WG 層連線監測小容器。供應鏈：alpine + python3（stdlib only，無 pip 依賴）+ 上游
# wireguard-tools（讀 `wg show`）。無中國實體、無 SaaS、無第三方 pip 套件 → /security-review 面極小。
FROM alpine:3.21

# python3：跑監測（純 stdlib：sqlite3/urllib/subprocess）。wireguard-tools：`wg show <iface> dump`。
RUN apk add --no-cache python3 wireguard-tools

WORKDIR /app
COPY src/ /app/

# NET_ADMIN（讀 wg netlink）+ 共享 ics-wg netns 由 compose 賦予。DB 落 /data（compose 掛持久卷）。
ENTRYPOINT ["python3", "/app/monitor.py"]
