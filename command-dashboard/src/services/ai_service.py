"""
ai_service.py — AI 整合抽象層（C0 stub）

設計原則：
- 依賴注入：底層 LLM client 可替換，router 不感知
- 本地優先：Breeze（Ollama，OpenAI-compatible API）
- 斷網可用：本地模型 fallback，雲端為 config 控制選項
- 法律責任：建議必須標記「僅供參考」，指揮官永遠有 override 權

C0 階段：stub 實作，Wave 5 接 Breeze/Ollama 實際呼叫。
"""

from repositories.ai_repo import create_recommendation, get_training_export


def get_recommendation(exercise_id: int | None,
                       recommendation_type: str,
                       cop_context: dict | None = None) -> dict:
    """
    即時建議（讀當下 COP 狀態）。
    C0 stub：直接回傳固定訊息，Wave 5 替換為 LLM 呼叫。
    """
    content    = "[AI 建議功能 Wave 5 啟用] 目前為 stub 模式，尚未連接語言模型。"
    confidence = None

    rec = create_recommendation(exercise_id, recommendation_type, content, confidence)
    return {
        "id":                  rec["id"],
        "recommendation_type": recommendation_type,
        "content":             content,
        "confidence":          confidence,
        "disclaimer":          "本建議僅供參考，指揮官保有最終決策權",
        "made_at":             rec["made_at"],
    }


def get_post_exercise_report(exercise_id: int) -> dict:
    """演練後分析（Wave 5 接 LLM，C0 回傳 stub）"""
    training_data = get_training_export(exercise_id)
    return {
        "exercise_id":    exercise_id,
        "summary":        "[演練後分析 Wave 5 啟用] stub 模式",
        "recommendations": [],
        "training_samples": len(training_data),
    }


def get_ml_export(exercise_id: int) -> list[dict]:
    """ML 訓練資料匯出（state/action/outcome 格式）"""
    return get_training_export(exercise_id)
