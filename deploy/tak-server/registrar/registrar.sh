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
# 協定（無外部依賴、injection-safe；不用 JSON，bash 解純文字行）：
#   請求：$QUEUE/requests/<id>.req  內容＝callsign / fingerprint / group / op（#398：第 4 行 op
#         選用，預設 register，向後相容既有三行請求）。dashboard 先寫 .tmp 再 rename，避免讀半寫。
#   op（#398 Slice 2）：
#     · register   ＝ usermod -f <fp> -g <group> <callsign>（建/改 cert-user）。
#     · deregister ＝ usermod -D <callsign>（從 TAK 移除 managed user；刪/撤連動，facet A）。
#     · reconcile  ＝ 讀 UserAuthenticationFile.xml 回 TAK 端真相（facet B；無使用者輸入、不碰 usermod）。
#   結果：$QUEUE/results/<id>.res   首 token＝OK|ERR，其後為訊息 / reconcile 的 callsign<TAB>fp 列。
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
# #398 B：reconcile 讀此檔取 TAK 端 cert-user → fingerprint 真相（與 usermod 同一份 SoT）。
AUTH_FILE="${USER_AUTH_FILE:-/opt/tak/UserAuthenticationFile.xml}"

# fingerprint＝SHA-256 冒號分隔大小寫 hex（openssl x509 -fingerprint -sha256 原樣，32 組）。
FP_RE='^([0-9A-Fa-f]{2}:){31}[0-9A-Fa-f]{2}$'
# callsign＝cert CN，對齊 dashboard is_valid_cert_cn（限字母/數字/空白/-_.@，不可逗號、不可 - 開頭）。
# 首字不可為 '-'（否則被 UserManager 當 flag 解析）或空白；其後才容 '-' 與空白。此為獨立防線
# （即使 dashboard 已擋；本容器在 takserver netns、跑 root usermod = 敏感，不可只靠上游驗）。
CN_RE='^[A-Za-z0-9_.@][A-Za-z0-9 ._@-]{0,63}$'

log() { echo "[registrar $(date -u +%FT%TZ)] $*"; }

valid_group() { case "$1" in neutral | red | blue) return 0 ;; *) return 1 ;; esac; }

# #398 review：infra cert-user 防護線——ICS 自身連線(ics-cot)/管理(ics-tak-admin) 證**不可**被
# deregister（usermod -D）。即使上游 dashboard 已擋，本容器跑 root usermod = 敏感，且佇列 0777
# 共享、被攻陷的 web tier 可直寫請求 → 此處獨立 denylist 防「刪掉 ICS 自己的 TAK 身分」blast radius。
is_infra_user() { case "$1" in ics-cot | ics-tak-admin) return 0 ;; *) return 1 ;; esac; }

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
    # 四行：callsign / fingerprint / group / op（#398：op 選用，預設 register，向後相容既有三行請求）。
    mapfile -t L <"$f" || true
    callsign="${L[0]:-}"
    fp="${L[1]:-}"
    group="${L[2]:-neutral}"
    op="${L[3]:-register}"
    op="${op%$'\r'}"  # 防 CRLF 殘留（ICS 寫 \n，但戒慎）
    # 請求檔 .req 在「結果寫完後」才刪（見各分支末），達 at-least-once：crash 重跑（usermod 冪等）不丟件。

    case "$op" in
      register)
        # usermod -f <fp> -g <group> <callsign>：建/改 cert-user（-g = in+out 群權限）。連本機 IPC 熱套用。
        if ! [[ "$callsign" =~ $CN_RE ]] || ! [[ "$fp" =~ $FP_RE ]] || ! valid_group "$group"; then
          log "reject $id: bad-input (register callsign/fp/group 格式不符)"
          echo "ERR bad-input" >"$RES_DIR/$id.res.tmp" && mv "$RES_DIR/$id.res.tmp" "$RES_DIR/$id.res"
        else
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
        fi
        ;;
      deregister)
        # #398 A：從 TAK 移除 managed user（usermod -D = --delete-user）。只需 callsign（fp/group 忽略）。
        if ! [[ "$callsign" =~ $CN_RE ]]; then
          log "reject $id: bad-input (deregister callsign 格式不符)"
          echo "ERR bad-input" >"$RES_DIR/$id.res.tmp" && mv "$RES_DIR/$id.res.tmp" "$RES_DIR/$id.res"
        elif is_infra_user "$callsign"; then
          # review：拒刪 ICS 自身/管理證——保護 ICS 的 TAK 控制面（防 footgun / 被攻陷 web tier）。
          log "reject $id: infra-protected deregister '$callsign'"
          echo "ERR infra-protected" >"$RES_DIR/$id.res.tmp" && mv "$RES_DIR/$id.res.tmp" "$RES_DIR/$id.res"
        else
          out="$(cd /opt/tak && java -jar "$JAR" usermod -D "$callsign" 2>&1)"
          rc=$?
          if [ "$rc" -eq 0 ]; then
            log "deregistered $callsign"
            echo "OK deregistered" >"$RES_DIR/$id.res.tmp" && mv "$RES_DIR/$id.res.tmp" "$RES_DIR/$id.res"
          else
            msg="${out//$'\n'/ }"
            log "usermod -D FAIL $callsign rc=$rc: $msg"
            printf 'ERR rc=%s %s\n' "$rc" "$msg" >"$RES_DIR/$id.res.tmp" && mv "$RES_DIR/$id.res.tmp" "$RES_DIR/$id.res"
          fi
        fi
        ;;
      reconcile)
        # #398 B：回 TAK 端真相——讀 UserAuthenticationFile.xml 取每個 cert-user 的 fingerprint（無使用者
        # 輸入、不碰 usermod）。輸出首行 'OK reconcile <count>'，其後每行 callsign<TAB>fingerprint。
        # review：取整個 <User …> 元素（含兩屬性的才要），再各自抽 identifier/fingerprint——**不依賴
        # 屬性相鄰或順序**（usermod marshaller / register-tak-fingerprint.sh 會插 role= 在中間，
        # 舊「相鄰」pattern 會漏抓 → reconcile 誤報全未同步）。grep -F 過濾兩屬性都在。
        mapfile -t USERS < <(grep -oE '<User [^>]*>' "$AUTH_FILE" 2>/dev/null | grep -F 'identifier=' | grep -F 'fingerprint=' || true)
        {
          printf 'OK reconcile %s\n' "${#USERS[@]}"
          for u in "${USERS[@]}"; do
            uid_="$(printf '%s' "$u" | sed -E 's/.*identifier="([^"]+)".*/\1/')"
            ufp="$(printf '%s' "$u" | sed -E 's/.*fingerprint="([0-9A-Fa-f:]+)".*/\1/')"
            printf '%s\t%s\n' "$uid_" "$ufp"
          done
        } >"$RES_DIR/$id.res.tmp" && mv "$RES_DIR/$id.res.tmp" "$RES_DIR/$id.res"
        log "reconcile -> ${#USERS[@]} users"
        ;;
      *)
        log "reject $id: unknown-op '$op'"
        echo "ERR unknown-op" >"$RES_DIR/$id.res.tmp" && mv "$RES_DIR/$id.res.tmp" "$RES_DIR/$id.res"
        ;;
    esac
    rm -f "$f"  # 結果已落地，刪請求（at-least-once：crash 重跑冪等）
  done
  shopt -u nullglob
  sleep "$POLL_S"
done
