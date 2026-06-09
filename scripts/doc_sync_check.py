#!/usr/bin/env python3
"""ICS_Command doc-sync checker

驗證 CLAUDE.md / docs/ROADMAP.md / docs/PROCESS.md 引用的程式路徑真實存在，
偵測 doc-vs-code drift。Exit 0 = pass, 1 = warnings.

借鏡自 WaveInk scripts/doc_sync_check.py，加 ROADMAP item 反向 grep。
"""

import re
import subprocess
import sys
from pathlib import Path

# Windows console 預設 cp950，print 中文 / ✓⚠ 符號會 UnicodeEncodeError（#148）；
# 強制 stdout/stderr UTF-8。hasattr 守門：stdout 被重導/替換（如 pytest capture）時無 reconfigure。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent.parent
WARNINGS: list[str] = []


def _is_gitignored(ref: str) -> bool:
    """ref 指向 .gitignore 涵蓋的 runtime / derived 路徑（如 data/ 下的 runtime 檔、
    badge.png）時，刻意不在 git 內是正確的，不算 doc drift。

    對應 CLAUDE.md〈User Data 邊界〉：data/ 為 gitignored runtime，啟動時由
    static/<name>.seed.<ext> 兜底生成 —— doc 記錄這條路徑是對的，checker 不該誤報。
    """
    try:
        r = subprocess.run(
            ["git", "-C", str(BASE), "check-ignore", "-q", ref],
            capture_output=True,
            check=False,
        )
        return r.returncode == 0
    except OSError:
        return False


def warn(msg: str):
    WARNINGS.append(msg)


# ─────────────────────────────────────────────────────────────
# 1. CLAUDE.md / ROADMAP.md / PROCESS.md 引用的路徑要存在
# ─────────────────────────────────────────────────────────────
# Markdown 路徑提取：matches `path/to/file.py`、(path/to/file.md)、[label](path)
PATH_PATTERNS = [
    re.compile(r"`([a-zA-Z0-9_\-./]+\.(?:py|js|md|html|css|json|yml|yaml|sh|toml))`"),
    re.compile(
        r"\[[^\]]+\]\(([a-zA-Z0-9_\-./]+\.(?:py|js|md|html|css|json|yml|yaml|sh|toml))\)"
    ),
]

# 不檢查這些（外部 URL、placeholder、模板示意）
SKIP_PATHS = {
    "command-vX.Y.Z",  # 舊 tag 前綴 placeholder（歷史引用仍在）
    "backend-vX.Y.Z",  # 新 tag 前綴 placeholder（2026-06-08 改名）
    "frontend-vX.Y.Z",
    "<branch>",
    "<指令>",
    "static/badge.png",  # gitignored 但 README 提到
}


def extract_paths(text: str) -> set[str]:
    paths = set()
    for p in PATH_PATTERNS:
        for m in p.finditer(text):
            path = m.group(1)
            if path.startswith(("http://", "https://", "/")):
                continue
            if path in SKIP_PATHS:
                continue
            # 解析 ../ 相對路徑
            paths.add(path)
    return paths


# Code path（routers / services / repositories）由 check_roadmap_code_refs() 專管
CODE_PATH_PREFIXES = ("routers/", "services/", "repositories/")


def is_code_ref(ref: str) -> bool:
    return any(ref.startswith(p) for p in CODE_PATH_PREFIXES)


def check_doc(doc_path: Path):
    """通用 doc 引用檢查。
    ROADMAP.md 例外：屬 planning doc，引用未來 phase 檔案是正常的；
    code refs 一律交給 check_roadmap_code_refs() 專管。
    """
    if not doc_path.exists():
        warn(f"{doc_path.name} 不存在")
        return
    text = doc_path.read_text(encoding="utf-8")
    doc_dir = doc_path.parent
    is_roadmap = doc_path.name == "ROADMAP.md"
    for ref in extract_paths(text):
        if is_code_ref(ref):
            continue  # 交給 specific check
        if is_roadmap:
            continue  # ROADMAP 是 planning，未來檔案不算 drift
        # 三種解析：相對 doc、相對 repo root、相對 command-dashboard/src
        candidates = [
            doc_dir / ref,
            BASE / ref,
            BASE / "command-dashboard" / "src" / ref,
        ]
        if not any(c.exists() for c in candidates):
            if _is_gitignored(ref):
                continue  # gitignored runtime / derived 路徑，doc 記錄正確（見 _is_gitignored）
            warn(f"{doc_path.relative_to(BASE)} 引用不存在的路徑：{ref}")


# ─────────────────────────────────────────────────────────────
# 2. ROADMAP 點名的具體 router / service / repo 檔案要存在
# ─────────────────────────────────────────────────────────────
# 例如 ROADMAP 寫 `routers/pi_push.py` 應對應 command-dashboard/src/routers/pi_push.py
ROADMAP_CODE_REF = re.compile(
    r"`(routers/[a-z_]+\.py|services/[a-z_]+\.py|repositories/[a-z_]+\.py)`"
)
SRC_ROOT = BASE / "command-dashboard" / "src"


def check_roadmap_code_refs():
    roadmap = BASE / "docs" / "ROADMAP.md"
    if not roadmap.exists():
        return
    text = roadmap.read_text(encoding="utf-8")
    for m in ROADMAP_CODE_REF.finditer(text):
        rel = m.group(1)
        # 容許「改名 ingress.py」這種 P1 預告 → 名單外的不算錯，只 info
        full = SRC_ROOT / rel
        if not full.exists():
            # phase 後續才生的檔案（依 ROADMAP 規劃）不算 drift
            future_files = {
                "ingress.py",  # P1-02
                "tak_service.py",  # P2-02
                "chat_service.py",  # P2-07
                "geometry_service.py",  # P2-08
                "tak_rest_client.py",  # P2-11
                "scenario_service.py",  # P2-19
                "datasync_service.py",  # P2-14
                "waveink_service.py",  # P3-03
                "weather_store.py",  # SC1（天氣/環境 feed，唯讀疊層，仿 facilities_store）
            }
            if full.name in future_files:
                continue
            warn(f"ROADMAP 引用 src/{rel} 不存在")


# ─────────────────────────────────────────────────────────────
# 3. CLAUDE.md / PROCESS.md / ROADMAP 三者互相引用要對得上
# ─────────────────────────────────────────────────────────────
def check_cross_refs():
    claude_md = BASE / "CLAUDE.md"
    process_md = BASE / "docs" / "PROCESS.md"
    roadmap_md = BASE / "docs" / "ROADMAP.md"
    for d in (claude_md, process_md, roadmap_md):
        if not d.exists():
            warn(f"核心 doc 缺檔：{d.relative_to(BASE)}")


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────
def main() -> int:
    check_doc(BASE / "CLAUDE.md")
    check_doc(BASE / "README.md")
    check_doc(BASE / "docs" / "ROADMAP.md")
    check_doc(BASE / "docs" / "PROCESS.md")
    check_roadmap_code_refs()
    check_cross_refs()

    if WARNINGS:
        print(f"\n⚠  doc-sync: {len(WARNINGS)} warning(s)\n")
        for i, w in enumerate(WARNINGS, 1):
            print(f"  {i}. {w}")
        print()
        return 1
    print("✓ doc-sync check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
