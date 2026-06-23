#!/usr/bin/env bash
# registrar.sh — #344 TAK 裝置 enrollment registrar（netns sidecar 的 watcher）。
#
# 為什麼存在（2026-06-23 PoC 實證，見 GitHub #344 / memory tak-faction-group-identifier）：
#   TAK 的 UserManager.jar（usermod/certmod）**不是檔案編輯器**——它連 takserver 的本機 IPC
#   把變更熱套用。所以：
#     · 直接編 UserAuthenticationFile.xml → 跑著的 server 不重讀（get-groups-for-user 500），
#       且下次 usermod re-marshal 會把直接編的 entry 清掉 → 裸寫檔死路。
#     · 獨立容器（自己 netns）跑 usermod → timeout（連不到 server 本機 IPC）。
#     · 本容器與 takserver **共用 network namespace**（compose `network_mode: service:takserver`）
#       → localhost IPC 可達 → usermod 成功、即時生效、免 restart、免 docker socket。
#
# 定位：dashboard（Python image、獨立 netns、無 Java）發證後無法自己跑 usermod。本 watcher 在
#   takserver netns 內、reuse ics-takserver image（含 Java + UserManager.jar）、掛同一份 /opt/tak，
#   經**共享卷檔佇列**收 dashboard 的註冊請求 → 跑 usermod 把裝置證 fingerprint 註冊成 managed user
#   + 指派初始群（預設 neutral，fail-closed）。之後 admin 紅藍分類走 REST update-groups（#363）。
#
# 協定（無外部依賴、injection-safe；不用 JSON，bash 解三行純文字）：
#   請求：$QUEUE/requests/<id>.req  內容三行＝callsign / fingerprint / group
#         （dashboard 先寫 .tmp 再 rename 進來，避免讀到半寫）。
#   結果：$QUEUE/results/<id>.res   首 token＝OK|ERR，其後為訊息。
#   watcher 處理後刪請求檔；dashboard 輪詢結果檔（逾時 = best-effort 跳過）。
#
# 安全：fingerprint / callsign / group 一律格式驗證後才餵 usermod（defense-in-depth，
#   即使 dashboard 已驗；本容器在 takserver netns = 敏感）。變數全 quote、無 eval。

set -uo pipefail

QUEUE="${REGISTRAR_QUEUE:-/registrar-queue}"
REQ_DIR="$QUEUE/requests"
RES_DIR="$QUEUE/results"
JAR="${USERMANAGER_JAR:-/opt/tak/utils/UserManager.jar}"
POLL_S="${REGISTRAR_POLL_S:-1}"

# fingerprint＝SHA-256 冒號分隔大小寫 hex（openssl x509 -fingerprint -sha256 原樣，32 組）。
FP_RE='^([0-9A-Fa-f]{2}:){31}[0-9A-Fa-f]{2}$'
# callsign＝cert CN，對齊 dashboard is_valid_cert_cn（限字母/數字/空白/-_.@，不可逗號、不可 - 開頭）。
# 首字不可為 '-'（否則被 UserManager 當 flag 解析）或空白；其後才容 '-' 與空白。此為獨立防線
# （即使 dashboard 已擋；本容器在 takserver netns、跑 root usermod = 敏感，不可只靠上游驗）。
CN_RE='^[A-Za-z0-9_.@][A-Za-z0-9 ._@-]{0,63}$'

log() { echo "[registrar $(date -u +%FT%TZ)] $*"; }

valid_group() { case "$1" in neutral | red | blue) return 0 ;; *) return 1 ;; esac; }

mkdir -p "$REQ_DIR" "$RES_DIR"
# 佇列由 root（本容器，takserver image）建，但消費端 ics-command 跑**非 root**（uid 10001 `ics`）→
# root:root 755 會讓 ICS 寫請求檔 Permission denied、enroll 靜默失敗。0777（無 sticky）：兩個受信
# 內部容器互寫/互刪佇列檔（協定即如此，ICS 寫 .req、registrar 寫 .res、各自刪對方檔）；此卷僅此二者掛載。
chmod 0777 "$QUEUE" "$REQ_DIR" "$RES_DIR" 2>/dev/null || true
log "watcher up; queue=$QUEUE jar=$JAR poll=${POLL_S}s（dirs chmod 0777，容非 root ICS 可寫）"

while true; do
  shopt -s nullglob
  for f in "$REQ_DIR"/*.req; do
    id="$(basename "$f" .req)"
    # 三行：callsign / fingerprint / group。mapfile 容空尾行。
    mapfile -t L <"$f" || true
    callsign="${L[0]:-}"
    fp="${L[1]:-}"
    group="${L[2]:-neutral}"
    # 注意：請求檔 .req **不在此刪**，而是在「結果寫完後」才刪（見各分支末），達 at-least-once：
    # 若 crash 在 usermod 後、刪檔前 → 重啟重跑（usermod -f 為 replace、冪等，無害），不會靜默丟件。

    if ! [[ "$callsign" =~ $CN_RE ]] || ! [[ "$fp" =~ $FP_RE ]] || ! valid_group "$group"; then
      log "reject $id: bad-input (callsign/fp/group 格式不符)"
      echo "ERR bad-input" >"$RES_DIR/$id.res.tmp" && mv "$RES_DIR/$id.res.tmp" "$RES_DIR/$id.res"
      rm -f "$f"
      continue
    fi

    # usermod -f <fp> -g <group> <callsign>：建/改 cert-user（-g = in+out 群權限）。
    # 連 takserver 本機 IPC 熱套用（共享 netns 才到得了）。
    out="$(cd /opt/tak && java -jar "$JAR" usermod -f "$fp" -g "$group" "$callsign" 2>&1)"
    rc=$?
    if [ "$rc" -eq 0 ]; then
      log "registered $callsign -> group=$group"
      echo "OK $group" >"$RES_DIR/$id.res.tmp" && mv "$RES_DIR/$id.res.tmp" "$RES_DIR/$id.res"
    else
      msg="${out//$'\n'/ }"
      log "usermod FAIL $callsign rc=$rc: $msg"
      printf 'ERR rc=%s %s\n' "$rc" "$msg" >"$RES_DIR/$id.res.tmp" && mv "$RES_DIR/$id.res.tmp" "$RES_DIR/$id.res"
    fi
    rm -f "$f"  # 結果已落地，刪請求（at-least-once：crash 重跑冪等）
  done
  shopt -u nullglob
  sleep "$POLL_S"
done
