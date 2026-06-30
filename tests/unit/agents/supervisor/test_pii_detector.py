"""PIIDetector 单测(主文件 §3.10,🔴 P0 合规)。"""
import pytest

from spma.agents.supervisor.pii_detector import PIIDetector


def test_detects_chinese_phone():
    det = PIIDetector()
    masked, types = det.detect_and_mask("我的手机是13800138000")
    assert "phone_cn" in types
    assert "13800138000" not in masked
    assert "[REDACTED]" in masked


def test_detects_email():
    det = PIIDetector()
    masked, types = det.detect_and_mask("邮箱 user@example.com 谢谢")
    assert "email" in types
    assert "user@example.com" not in masked


def test_detects_id_card():
    det = PIIDetector()
    masked, types = det.detect_and_mask("身份证 110101199001011234")
    assert "id_card_cn" in types


def test_detects_multiple_pii_types():
    det = PIIDetector()
    masked, types = det.detect_and_mask("电话 13800138000 邮箱 a@b.com")
    assert "phone_cn" in types
    assert "email" in types


def test_no_pii_returns_empty_types():
    det = PIIDetector()
    masked, types = det.detect_and_mask("今天天气不错")
    assert types == []
    assert masked == "今天天气不错"


def test_should_bypass_llm_when_pii_present():
    det = PIIDetector()
    assert det.should_bypass_llm("电话 13800138000") is True
    assert det.should_bypass_llm("今天天气不错") is False