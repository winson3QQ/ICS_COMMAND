# 稽核狀態總覽（single-pane audit dashboard）

> **一頁看完被稽核的東西**：各控制項 → 狀態 → 證據 → 缺口。給稽核員 / 投標 / 自評。
> **這是「靜態快照」**；live 版(自動匯各掃描結果)請見文末〈升級為 live 儀表板〉。
> 更新紀律：每次安全/合規相關 PR merge 時同步本表(對齊 PROCESS 8.5 ROADMAP tick 精神)。
> 最後更新：2026-06-27。SoT：威脅分析見 [`threat_model.md`](threat_model.md)；政策見 [`security_policies.md`](security_policies.md)。

狀態圖例：✅ 達標　🟡 部分/可配置　❌ 未達/缺口　⏭️ 範圍外(待決)

---

## 1. 控制矩陣

### 身分與認證（NIST SP 800-63B / OWASP ASVS V2,V3）
| 控制 | 狀態 | 證據 | 缺口 |
|---|---|---|---|
| 多因子（PIN + mTLS 裝置證 = AAL2）| 🟡 | #275；`auth/service.py`、`routers/auth.py` | 預設 `ICS_MTLS_REQUIRED=false` 退 AAL1；PIN 4–6 數字破 §5.1.1.2 |
| 密碼雜湊（PBKDF2 600k + salt + rehash + 等時比較）| ✅ | `repositories/_helpers.py` | — |
| Session（CSPRNG token、cert-bound、idle+absolute timeout、即時撤銷）| ✅ | `auth/service.py`；#275 | 14h 絕對逾時不滑動(#345)；token 存 sessionStorage(#293) |
| 帳號鎖定 / 登入限流 | 🟡 | `account_repo.py`、`auth/rate_limit.py` | lockout-DoS(mTLS off 時) #295 |

### 存取控制（NIST AC-3/6 / ASVS V4）
| 中央化 RBAC 強制（每 `/api/*`）| ✅ | `auth/middleware.py`、`role_enum.py` | — |
| **default-deny 兜底 + 路由分類回歸測試** | ✅ | #370；`test_rbac_route_matrix.py`（golden + fallthrough）| —（原 #348-F4 已解）|
| 跨演習 IDOR / scope | ✅ | #288；`exercise_service.resolve_scope` | — |

### 稽核日誌（NIST AU / ASVS V7）
| 寫入覆蓋（含 login/RBAC-deny/session-fail、correlation/exercise scope）| ✅ | `repositories/_helpers.audit`；82 處 | — |
| **完整性（防竄改）** | 🟡 | hash chain + **boot/endpoint 驗證（#372）** + **prod `audit_log` 引擎層 append-only 觸發器 + reset 不清 audit（GAP2）** | 剩 keyless 鏈可 recompute 偽造 → audit-v1 HMAC（需金鑰來源決策）；host-compromise 可 DROP 觸發器（§8.8 殘留）|
| 異常偵測（成功動作）| 🟡 | `security_monitor.py`（僅認證類、in-memory）| 位置跳變/大量刪除未做 #285 |

### 資料保護（NIST SC-28 / MP / ISO A.8）
| at-rest 加密（SQLCipher）| 🟡 | #229；driver+migration+整合測試齊 | prod **預設未開** → #348 GAP1（待實機）|
| 備份加密（Fernet `.enc`）| ✅ | `user_data_backup_service.py`；#228 | 中間快照短暫明文、無 rotation |
| PII 保存期限 | 🟡 | `retention_service.py`（tracks 90d）| events/chats/session-IP 無 TTL #348-F10 |

### 金鑰 / 信任根（NIST SC-12）
| 金鑰階層（FIDO2→HKDF）| 🟡 | `scripts/keymgmt/`；#227 | 硬體未驗 #230 |
| **CA 私鑰與 host 同機** | ❌ | `threat_model.md` §8.4 | 信任根單點 → #323（待 LUKS #231）|
| 憑證撤銷 | 🟡 | #318（TAK 真撤銷 Slice 1–3）| ICS mTLS 軌 #232 未竟 |

### 周邊 / AppSec（ASVS V5/13/14 / OWASP Top10）
| TLS 1.2/1.3 + 安全標頭 + CSP | 🟡 | #289；`deploy/nginx/` | 次要頁 CSP report-only #348-F14 |
| 注入（SQL 參數化 / 路徑遍歷）| ✅ | 全參數化 | tile 路由殘留(低)#348 |
| 程式 SAST（bandit）| ✅ | **0 HIGH / 0 actionable**（2026-06-27）| 10 MEDIUM B608 全 false-positive（參數化 SQL，f-string 僅含 `?` placeholder/欄位名/常數 scope，值經 bind——逐一驗）；B105 `'ics.session.'`=WS 子協定前綴非密碼；24 LOW=try/except-pass 等慣例 |
| CSRF（custom header 非 cookie）| ✅ | `auth/middleware.py` | — |
| proxy 信任 fail-fast | ✅ | #290；`main.py:95` | — |
| 黑箱滲測 | ❌ | — | 未做 #301 |

### 供應鏈 / SBOM（EO 14028 / NTIA / EU CRA）
| SBOM（每 release 產 + 烤入映像 + 可下載）| ✅ | #416/#419；`gen_release_sbom.sh`、`GET /api/sbom` | — |
| 漏洞掃描 | ✅ | **grype 基線 0 Critical/High/Medium/Low**（2026-06-27）| 48 筆 base-OS Unknown/Negligible（非 ICS）|
| VEX / 漏洞管理流程 | ✅ | #422；`sbom/VEX-README.md` | — |
| SBOM 真實性（簽章）| ❌ | — | cosign 待 registry+金鑰+CI #417 |
| 授權合規（自有 + 第三方）| ✅ | #351；`LICENSE`/`NOTICE`/`licenses/`/SPDX 檔頭 | LICENSE 地址/email/管轄待填、律師覆核 |
| 供應鏈紅線（無中國元件）| ✅ | CLAUDE.md；crypto 相依 US/歐 | — |

### 韌性 / 業務持續（ISO 22301 / NIST CP）
| 降階 doctrine（TAK 掛掉走三階）| ✅ | strategy §2 | — |
| HA / DR 演練 | ❌ | — | 單機、無 DR 演練 #348-F9 |

### 治理 / ISMS（ISO 27001 / 資安管理法）
| ISMS 政策文件 | 🟡 | `security_policies.md`（§1–6 + 附錄 A 內文補實，self-attestation）| 第三方(ISO 27001) 驗證待 auditor；技術缺口見各列 → #348-F11 |
| 威脅模型 | ✅ | `threat_model.md`（誠實、與 code 相符）| — |
| 持續監控 / SOC | ❌ | — | 無 |

### 應變管理領域互通（FEMA/CAP/EDXL — #349）
| 地理空間互通（TAK/CoT、2525、MGRS）| ✅ | — | — |
| CAP-TWP 官方示警入向 | ⏭️ | — | 待 scope 決策 #349-D1 |
| EDXL / NIMS / 無障礙 | ⏭️ | — | 待 scope #349 |

---

## 2. 標準對照（彙整判定）
- NIST 800-63B：配置後 AAL2 possession ✅ / 知識因子(PIN) ❌ / 預設退 AAL1。
- NIST 800-53：AC ✅(default-deny)、AU-9(3) 🟡（驗證接線 #372 + prod append-only；keyless HMAC 待）、SC-28 🟡、**SC-12 ❌(CA 同機)**、CP 🟡。
- OWASP ASVS：V4 ✅、V5/13/14 多 ✅、V2/V3/V7 🟡。
- FIPS 140：未跑 validated 模組 ❌。
- ISO 27001 ISMS：文件層 🟡（政策內文已補實=self-attestation；第三方驗證待 auditor）。
- 供應鏈 SBOM（EO/CRA/NTIA）：🟡→大致就緒（差 cosign 簽章）。

## 3. 最新掃描基線
- **grype SCA（2026-06-27，image `release-ea86203`）**：Critical 0 / High 0 / Medium 0 / Low 0；Unknown 39 + Negligible 9（全 Debian base-OS，非 ICS 相依）。詳 [`sbom/VEX-README.md`](../../sbom/VEX-README.md) §3。
- **bandit SAST（2026-06-27，`src/`，bandit 1.9.4）**：0 HIGH；10 MEDIUM（B608 SQL）+ 24 LOW **經 triage 全為 false-positive / 慣例**（SQL 全參數化、B105 為子協定前綴、B110 try/except-pass 刻意）→ **0 actionable**。
- 每 release 須重掃（grype 漏洞 DB 每日更新；SAST 隨 code 變動）。

## 4. 待補缺口（owner）
| 缺口 | 單 | owner |
|---|---|---|
| at-rest 加密啟用（實機 migration + DB_KEY 來源）| #348 GAP1 | 使用者+工程 |
| 稽核完整性閉環（接驗證器 + HMAC/簽章）| #348 GAP2 | 工程 |
| ISMS 文件補實 | #348 F11 | 使用者+工程 |
| HA/DR 演練 | #348 F9 | 使用者 |
| 黑箱滲測 | #301 | 使用者+工程 |
| cosign 簽章 | #417 | 待 registry/CI |
| LICENSE 欄位 + 律師覆核 | #351 | 使用者+律師 |
| CI 恢復（billing / 自架 runner）| — | 使用者 |

## 5. 升級為 live 儀表板
本頁是靜態快照。自動化版（CI 復活後）：
- **OWASP DefectDojo**（自架）：各掃描（grype/bandit/SAST/DAST/滲測）結果自動匯入、去重、追 finding 生命週期 → 取代本表手動維護。
- **Dependency-Track**（自架）：SBOM/SCA 專用儀表板 + 新 CVE 告警。
- CI 各 job 跑完用各自 uploader 推進上述平台；本頁保留為「人可讀的控制矩陣 + 證據索引」。
