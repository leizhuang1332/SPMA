"""Prompt 注入检测 + 清洗(主文件 §3.10,🔴 P0 安全)。"""
import re
import logging

logger = logging.getLogger(__name__)


class PromptInjectionGuard:
    INJECTION_PATTERNS = [
        re.compile(r"ignore\s+(previous|above|all)\s+instructions?", re.I),
        re.compile(r"you\s+are\s+now\s+", re.I),
        re.compile(r"system\s*:\s*", re.I),
        re.compile(r"<\s*\|.*\|\s*>", re.I),
        re.compile(r"\{\{.*\}\}", re.I),
    ]

    def is_suspicious(self, text: str) -> bool:
        return any(p.search(text) for p in self.INJECTION_PATTERNS)

    def sanitize(self, text: str) -> str:
        sanitized = text
        for p in self.INJECTION_PATTERNS:
            sanitized = p.sub("[FILTERED]", sanitized)
        return sanitized
