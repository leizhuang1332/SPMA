"""个人敏感信息检测 + 脱敏(主文件 §3.10,🔴 P0 合规)。

支持中文语境下的 PII 检测 —— 不能用 \\b 作为边界(中文不属于 \\w,
Python re 默认 \\b 在中文旁不形成单词边界)。改用 (?<![0-9A-Za-z]) 和
(?![0-9A-Za-z]) 确保:前后是中文/空格/标点都允许,前后是数字或字母则拒绝。
"""
import re
import logging

logger = logging.getLogger(__name__)


class PIIDetector:
    PII_PATTERNS = {
        "phone_cn": re.compile(r"(?<![0-9A-Za-z])1[3-9]\d{9}(?![0-9A-Za-z])"),
        "id_card_cn": re.compile(r"(?<![0-9A-Za-z])\d{17}[\dXx](?![0-9A-Za-z])"),
        "email": re.compile(r"(?<![0-9A-Za-z])[\w.+-]+@[\w-]+\.[\w.-]+\b"),
        "credit_card": re.compile(r"(?<![0-9A-Za-z])(?:\d[ -]*?){13,16}(?![0-9A-Za-z])"),
        "ip_v4": re.compile(r"(?<![0-9A-Za-z.])(?:\d{1,3}\.){3}\d{1,3}(?![0-9A-Za-z.])"),
    }

    def __init__(self, mask_token: str = "[REDACTED]"):
        self._mask = mask_token

    def detect_and_mask(self, text: str) -> tuple[str, list[str]]:
        detected: list[str] = []
        masked = text
        for pii_type, pattern in self.PII_PATTERNS.items():
            if pattern.search(masked):
                detected.append(pii_type)
                masked = pattern.sub(self._mask, masked)
        return masked, detected

    def should_bypass_llm(self, text: str) -> bool:
        _, detected = self.detect_and_mask(text)
        return len(detected) > 0