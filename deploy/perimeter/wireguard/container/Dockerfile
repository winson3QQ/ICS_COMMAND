# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
#
# #434 容器化 WireGuard server（ICS 可驅動）。供應鏈：alpine + 上游 wireguard-tools/iptables/iproute2，
# 無中國實體、無 SaaS。kernel WG（WSL2 6.6 / mainline 原生支援，已實測），非 userspace。
FROM alpine:3.21

# bash（watcher 用）、wireguard-tools（wg/wg-quick）、iptables（DNAT 路由）、iproute2（ip link）。
RUN apk add --no-cache bash wireguard-tools iptables iproute2

COPY entrypoint.sh wg-peer-registrar.sh wg-persist.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/wg-peer-registrar.sh /usr/local/bin/wg-persist.sh

# NET_ADMIN + /dev/net/tun 由 compose 賦予；wg0 + DNAT 在 entrypoint 套用。
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
