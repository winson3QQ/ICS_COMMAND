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
#   op（#398 Slice 2 / #404）：
#     · register   ＝ usermod -f <fp> -g <group> <callsign>（建/改 cert-user）。
#     · deregister ＝ usermod -D <callsign>（從 TAK 移除 managed user；刪/撤連動，facet A）。
#     · reconcile  ＝ 讀 UserAuthenticationFile.xml 回 TAK 端真相（facet B；無使用者輸入、不碰 usermod）。
#     · strip-anon ＝ usermod -f <fp> -r -g __ANON__ <callsign>（#404：移出匿名群、保留其餘群
#                    → 修 producer 卡 __ANON__ 與任何 CA 證同頻的隔離破口）。
#   結果：$QUEUE/results/<id>.res   首 token＝OK|ERR，其後為訊息 / reconcile 的
#         callsign<TAB>fp<TAB>group1,group2,... 列（#404：reconcile 第 3 欄群清單，舊式兩欄 client 忽略即相容）。
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
        # #398 B + #404：回 TAK 端真相——讀 UserAuthenticationFile.xml 取每個 cert-user 的
        # fingerprint + groupList（無使用者輸入、不碰 usermod）。輸出首行 'OK reconcile <count>'，
        # 其後每行 callsign<TAB>fingerprint<TAB>group1,group2,...（#404：第 3 欄群清單，供 ICS
        # 偵測 producer 卡 __ANON__ 隔離破口；舊式兩欄 client 忽略第 3 欄即向後相容）。
        # awk 逐行 walk，**兼容單行與多行** User（usermod marshal=多行；register-tak-fingerprint.sh=單行）：
        # <User …> 起 → 抽同一行所有 inline <groupList>（單行格式）→ 同行 </User>/自閉即收尾；否則
        # 續收後續 <groupList> 行直到 </User>。只 emit identifier+fingerprint 皆在的（無 fp 的 --help 略過）。
        # 進新 <User> 前先收尾上一個未閉合的（防跨行 back-to-back 漏抓）。屬性不依賴相鄰/順序。
        # 假設**一行至多一個 User**（usermod marshal 與 register-tak-fingerprint.sh 皆如此）；兩 User
        # 擠同一行屬理論 malformed、不處理（inline group while-loop 會把兩者群併入前者，極罕見不 over-engineer）。
        out_rows="$(awk '
          function emit() { if (id != "" && fp != "") print id "\t" fp "\t" grp }
          /<User / {
            if (inu) emit()                                  # 收尾上一個未閉合的（back-to-back 防漏）
            id=""; fp=""; grp=""; inu=1
            if (match($0, /identifier="[^"]*"/)) id=substr($0,RSTART+12,RLENGTH-13)
            if (match($0, /fingerprint="[^"]*"/)) fp=substr($0,RSTART+13,RLENGTH-14)
            line=$0                                          # 抽同一行 inline <groupList>（單行格式）
            while (match(line, /<groupList>[^<]*<\/groupList>/)) {
              g=substr(line,RSTART,RLENGTH); sub(/^<groupList>/,"",g); sub(/<\/groupList>$/,"",g)
              grp=(grp==""?g:grp","g); line=substr(line,RSTART+RLENGTH)
            }
            if ($0 ~ /\/>/ || $0 ~ /<\/User>/) { emit(); inu=0 }   # 單行收尾（自閉或同行 </User>）
            next
          }
          inu && /<groupList>/ {
            g=$0; sub(/.*<groupList>/,"",g); sub(/<\/groupList>.*/,"",g)
            grp=(grp==""?g:grp","g); next
          }
          inu && /<\/User>/ { emit(); inu=0; next }
          END { if (inu) emit() }                            # 檔尾未閉合的也收尾
        ' "$AUTH_FILE" 2>/dev/null)"
        n="$(printf '%s' "$out_rows" | grep -c . || true)"
        {
          printf 'OK reconcile %s\n' "$n"
          [ -n "$out_rows" ] && printf '%s\n' "$out_rows"
        } >"$RES_DIR/$id.res.tmp" && mv "$RES_DIR/$id.res.tmp" "$RES_DIR/$id.res"
        log "reconcile -> $n users"
        ;;
      strip-anon)
        # #404：移出 __ANON__ 匿名群（usermod -f <fp> -r -g __ANON__ <callsign>），保留其餘群 →
        # 修「producer 卡 __ANON__ = 與任何 CA 證同頻」隔離破口。需 callsign+fp（與 register 同驗、-f 確保
        # 不動憑證）。**不套 infra denylist**：ics-cot 正是要修的對象；ics-tak-admin 僅 __ANON__ → -r 後
        # 無群會 bounce 回 __ANON__（usermod 行為），等同 no-op、無害。
        if ! [[ "$callsign" =~ $CN_RE ]] || ! [[ "$fp" =~ $FP_RE ]]; then
          log "reject $id: bad-input (strip-anon callsign/fp 格式不符)"
          echo "ERR bad-input" >"$RES_DIR/$id.res.tmp" && mv "$RES_DIR/$id.res.tmp" "$RES_DIR/$id.res"
        else
          out="$(cd /opt/tak && java -jar "$JAR" usermod -f "$fp" -r -g __ANON__ "$callsign" 2>&1)"
          rc=$?
          if [ "$rc" -eq 0 ]; then
            log "stripped __ANON__ from $callsign"
            echo "OK stripped" >"$RES_DIR/$id.res.tmp" && mv "$RES_DIR/$id.res.tmp" "$RES_DIR/$id.res"
          else
            msg="${out//$'\n'/ }"
            log "usermod -r __ANON__ FAIL $callsign rc=$rc: $msg"
            printf 'ERR rc=%s %s\n' "$rc" "$msg" >"$RES_DIR/$id.res.tmp" && mv "$RES_DIR/$id.res.tmp" "$RES_DIR/$id.res"
          fi
        fi
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
