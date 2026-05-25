"""
core/security_headers.py — 安全 HTTP 標頭 middleware（C1-B + C1-F）

分工：
  - HSTS / X-Frame-Options / X-Content-Type-Options / Referrer-Policy：由 nginx 注入
    （見 deploy/nginx/conf.d/security-headers.conf）
  - CSP：由本 middleware 注入（與應用語義耦合）

CSP 路徑策略（C1-F RV2-02 雙白名單）：
  ENFORCE_PATHS   → Content-Security-Policy（enforce，無 unsafe-inline）
  REPORT_ONLY_PATHS → Content-Security-Policy-Report-Only
  其他路徑        → report-only（安全 fallback，防 silent regression）

  ⚠️  Adding new HTML routes MUST update one of these lists explicitly.
      New commander-related paths (e.g. commander_v2.html) MUST go to ENFORCE_PATHS.
      Leaving a path unclassified = falls to report-only (intentional safe default).
      PR review on ENFORCE_PATHS changes SHOULD trigger security review.

模式切換（全域 override）：環境變數 CSP_MODE=report-only|enforce
  - report-only：所有路徑一律送 Report-Only（觀察期、dev 環境）
  - enforce：依雙白名單分流（正式部署）
"""

from fastapi import Request
from fastapi.responses import Response

from core.config import CSP_MODE, CSP_REPORT_URI, ENABLE_SECURITY_HEADERS

# ── 路徑分類白名單（C1-F RV2-02）────────────────────────────────────────────
#
# ENFORCE_PATHS：已完成模組化，unsafe-inline 已移除的路徑。
#   切換為 Content-Security-Policy（enforce）。
#   新增成員前必須確認該頁面無 inline handler / inline script。
#
# REPORT_ONLY_PATHS：尚未完成模組化（W-C1-F 範圍）或純 admin 工具頁面。
#   維持 Content-Security-Policy-Report-Only。
#
# 清單以 command-dashboard/static/ 實際存在的 HTML 為準。
# 任何新增 /static/*.html 都必須明確歸入其中一個清單。

ENFORCE_PATHS: tuple[str, ...] = (
    "/static/commander_dashboard.html",
    # 未來新增 commander-related 路徑時加入此清單（需 security review）
)

REPORT_ONLY_PATHS: tuple[str, ...] = (
    "/static/admin_backups.html",
    "/static/scenario_designer.html",
    "/static/qr_scanner.html",
    "/static/icon_preview.html",
    # 其他 admin / 工具頁面加入此清單（待 W-C1-F 完成後逐步升 enforce）
)


def _build_csp(include_unsafe_inline: bool) -> str:
    """
    組 CSP 字串。
    include_unsafe_inline=True：report-only 路徑（兼容未重構頁面）
    include_unsafe_inline=False：enforce 路徑（已完成模組化）
    """
    script_src = (
        "script-src 'self' 'unsafe-inline'"
        if include_unsafe_inline
        else "script-src 'self'"
    )
    directives = [
        "default-src 'self'",
        script_src,
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "img-src 'self' data: blob:",
        "connect-src 'self' wss: ws:",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "worker-src 'self' blob:",
        "form-action 'self'",
        "base-uri 'self'",
        f"report-uri {CSP_REPORT_URI}",
    ]
    return "; ".join(directives)


# 預先組好兩版 CSP 字串，避免每次 request 重組
_CSP_ENFORCE    = _build_csp(include_unsafe_inline=False)
_CSP_REPORT_ONLY = _build_csp(include_unsafe_inline=True)


def _get_csp_header(path: str) -> tuple[str, str]:
    """
    依路徑決定 CSP header 名稱與值。
    回傳 (header_name, header_value)。

    全域 CSP_MODE=report-only 時，忽略白名單，一律 report-only（dev / 觀察期）。
    """
    if CSP_MODE == "report-only":
        return ("Content-Security-Policy-Report-Only", _CSP_REPORT_ONLY)

    if path in ENFORCE_PATHS:
        return ("Content-Security-Policy", _CSP_ENFORCE)

    # REPORT_ONLY_PATHS + 未分類路徑，一律 report-only（安全 fallback）
    return ("Content-Security-Policy-Report-Only", _CSP_REPORT_ONLY)


async def security_headers_middleware(request: Request, call_next) -> Response:
    """注入 CSP header；其他安全 header 由 nginx 負責。"""
    response = await call_next(request)
    if ENABLE_SECURITY_HEADERS:
        header_name, header_value = _get_csp_header(request.url.path)
        response.headers[header_name] = header_value
    return response
