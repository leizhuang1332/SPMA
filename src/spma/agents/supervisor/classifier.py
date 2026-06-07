"""意图分类器——LLM结构化分类 + 规则兜底两层架构。

设计依据: SPMA-design-01 第五节 意图分类器设计
"""

from spma.models.classification import ClassificationResult


async def classify_intent(user_query: str, context: dict) -> ClassificationResult:
    """LLM 意图分类（第一层）——确定需要哪些 Worker Agent。"""
    raise NotImplementedError


def rule_based_classification(user_query: str) -> ClassificationResult:
    """纯规则兜底（第二层）——LLM 不可用时使用。正则匹配 + 关键词词典，准确率约 85%。"""
    raise NotImplementedError
