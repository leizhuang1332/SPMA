"""数据脱敏——Presidio + 自定义规则。

Layer 1: Microsoft Presidio 通用 PII（手机号/邮箱/身份证/信用卡）
Layer 2: 自定义正则（内部IP/主机名/金额/API Key）
决策: 外网API→全部脱敏, 本地vLLM→可选脱敏

设计依据: SPMA-technology-selection §12 安全与合规
"""
