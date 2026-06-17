# SPMA 质量评分方案优化设计

## 1. 概述

本文档描述了 SPMA Supervisor 质量评分系统的优化方案。当前评分方案基于三维加权（count + confidence + exact_match），本优化建议旨在提升评估的严谨性和科学性，增加更多维度的评估指标。

## 2. 当前评分方案分析

### 2.1 现有指标体系

当前评分方案定义于 `src/spma/agents/supervisor/quality.py`，包含三个核心维度：

| 维度 | 计算方式 | 权重配置 |
|------|----------|----------|
| **count_score** | `min(1.0, result_count / 3.0)` | data_query: 0.3, search: 0.4, trace: 0.2 |
| **confidence_score** | `confidence` | data_query: 0.3, search: 0.4, trace: 0.3 |
| **exact_score** | `1.0 if has_exact_match else 0.0` | data_query: 0.4, search: 0.2, trace: 0.5 |

### 2.2 现有方案局限性

1. **缺乏排序感知**：`count_score` 只关注数量，未考虑结果排序质量
2. **置信度自报告偏差**：`confidence` 由 Worker 自行提供，可能存在系统性偏差
3. **精确匹配过于简单**：`has_exact_match` 只是布尔判断，无法识别同义表达
4. **维度不足**：缺少语义层面的评估指标

## 3. 优化方案

### 3.1 指标精细化改进

#### 3.1.1 结果数量指标：引入排序感知

**优化思路**：前序结果应具有更高权重，反映搜索结果的排序质量。

```python
def calculate_count_score(result_count: int, result_scores: list = None) -> float:
    """
    改进的数量得分计算，考虑结果排序质量
    
    Args:
        result_count: 返回结果总数
        result_scores: 每个结果的相关性分数列表（可选）
    
    Returns:
        加权后的数量得分
    """
    if result_count == 0:
        return 0.0
    
    if result_scores:
        # 前3个结果权重递减：0.5, 0.3, 0.2
        weights = [0.5, 0.3, 0.2]
        weighted_sum = sum(
            score * weights[i] 
            for i, score in enumerate(result_scores[:3])
        )
        return min(1.0, weighted_sum)
    else:
        # 上限调整为5个结果
        return min(1.0, result_count / 5.0)
```

#### 3.1.2 置信度指标：引入校准机制

**优化思路**：基于历史数据对 Worker 自报告的置信度进行校准。

```python
def calculate_confidence_score(confidence: float, worker_type: str, 
                            historical_data: dict = None) -> float:
    """
    改进的置信度得分计算，引入校准机制
    
    Args:
        confidence: Worker报告的置信度
        worker_type: Worker类型
        historical_data: 历史校准数据
    
    Returns:
        校准后的置信度得分
    """
    base_score = confidence
    
    if historical_data and worker_type in historical_data:
        calibration_factor = historical_data[worker_type].get('calibration', 1.0)
        base_score = confidence * calibration_factor
    
    return base_score
```

#### 3.1.3 精确匹配指标：引入语义匹配

**优化思路**：使用语义相似度模型替代简单的精确字符串匹配。

```python
def calculate_exact_score(answer: str, ground_truth: str = None, 
                        threshold: float = 0.8) -> float:
    """
    改进的精确匹配得分，引入语义相似度
    
    Args:
        answer: Worker返回的答案
        ground_truth: 标准答案（可选）
        threshold: 语义相似度阈值
    
    Returns:
        语义匹配得分（0-1）
    """
    if not ground_truth:
        return 1.0 if answer else 0.0
    
    try:
        from sentence_transformers import SentenceTransformer, util
        
        model = SentenceTransformer('all-MiniLM-L6-v2')
        emb1 = model.encode(answer, convert_to_tensor=True)
        emb2 = model.encode(ground_truth, convert_to_tensor=True)
        similarity = util.cos_sim(emb1, emb2).item()
        
        return 1.0 if similarity >= threshold else similarity
    except ImportError:
        return 1.0 if answer == ground_truth else 0.0
```

### 3.2 新增评估指标

#### 3.2.1 答案相关性指标

衡量答案与问题的语义相关性：

```python
def calculate_relevance_score(question: str, answer: str) -> float:
    """
    计算答案与问题的语义相关性
    
    Args:
        question: 用户问题
        answer: Worker返回的答案
    
    Returns:
        相关性得分（0-1）
    """
    try:
        from sentence_transformers import SentenceTransformer, util
        
        model = SentenceTransformer('all-MiniLM-L6-v2')
        emb_q = model.encode(question, convert_to_tensor=True)
        emb_a = model.encode(answer, convert_to_tensor=True)
        return util.cos_sim(emb_q, emb_a).item()
    except ImportError:
        q_words = set(question.lower().split())
        a_words = set(answer.lower().split())
        overlap = q_words.intersection(a_words)
        return len(overlap) / len(q_words) if q_words else 0.0
```

#### 3.2.2 答案完整性指标

衡量答案覆盖问题关键点的程度：

```python
def calculate_completeness_score(answer: str, expected_points: list = None) -> float:
    """
    计算答案完整性得分
    
    Args:
        answer: Worker返回的答案
        expected_points: 期望覆盖的关键点列表
    
    Returns:
        完整性得分（0-1）
    """
    if not expected_points:
        sentences = answer.count('.') + answer.count('?') + answer.count('!')
        return min(1.0, sentences / 5.0)
    
    covered = 0
    for point in expected_points:
        if point.lower() in answer.lower():
            covered += 1
    
    return covered / len(expected_points)
```

#### 3.2.3 答案简洁性指标

衡量答案是否简洁，避免冗余：

```python
def calculate_conciseness_score(question: str, answer: str) -> float:
    """
    计算答案简洁性得分
    
    Args:
        question: 用户问题
        answer: Worker返回的答案
    
    Returns:
        简洁性得分（0-1）
    """
    q_length = len(question.split())
    a_length = len(answer.split())
    
    ideal_min = q_length
    ideal_max = q_length * 3
    
    if ideal_min <= a_length <= ideal_max:
        return 1.0
    elif a_length < ideal_min:
        return (a_length / ideal_min) * 0.8
    else:
        return max(0.2, ideal_max / a_length)
```

#### 3.2.4 时效性指标（可选）

适用于需要最新信息的场景：

```python
def calculate_timeliness_score(timestamp: float, max_age_hours: int = 24) -> float:
    """
    计算结果时效性得分
    
    Args:
        timestamp: 结果的时间戳（秒）
        max_age_hours: 最大有效时长（小时）
    
    Returns:
        时效性得分（0-1）
    """
    import time
    
    age_hours = (time.time() - timestamp) / 3600
    if age_hours <= 0:
        return 1.0
    elif age_hours >= max_age_hours:
        return 0.1
    else:
        return 1.0 - (age_hours / max_age_hours) * 0.9
```

### 3.3 优化后的权重配置

| 指标 | data_query | search | trace |
|------|------------|--------|-------|
| count | 0.15 | 0.25 | 0.10 |
| confidence | 0.20 | 0.20 | 0.20 |
| exact_match | 0.25 | 0.10 | 0.35 |
| relevance | 0.20 | 0.20 | 0.15 |
| completeness | 0.15 | 0.15 | 0.15 |
| conciseness | 0.05 | 0.10 | 0.05 |
| **合计** | **1.00** | **1.00** | **1.00** |

### 3.4 优化后的完整评分函数

```python
"""Supervisor 质量评分——优化版：多维评估体系。"""

from typing import Optional, List, Dict
from spma.models.worker_output import WorkerOutput

QUALITY_WEIGHTS = {
    "data_query": {
        "count": 0.15, 
        "confidence": 0.20, 
        "exact_match": 0.25,
        "relevance": 0.20,
        "completeness": 0.15,
        "conciseness": 0.05
    },
    "search": {
        "count": 0.25, 
        "confidence": 0.20, 
        "exact_match": 0.10,
        "relevance": 0.20,
        "completeness": 0.15,
        "conciseness": 0.10
    },
    "trace": {
        "count": 0.10, 
        "confidence": 0.20, 
        "exact_match": 0.35,
        "relevance": 0.15,
        "completeness": 0.15,
        "conciseness": 0.05
    },
}

def score_worker(worker_output: WorkerOutput, query_type: str, 
                question: str = "") -> float:
    """优化后的评分函数"""
    weights = QUALITY_WEIGHTS.get(query_type, QUALITY_WEIGHTS["search"])
    
    count_score = calculate_count_score(
        worker_output.get("result_count", 0),
        worker_output.get("result_scores")
    ) * weights["count"]
    
    confidence = worker_output.get("confidence", 0) or 0
    confidence_score = confidence * weights["confidence"]
    
    has_exact = worker_output.get("has_exact_match", False)
    exact_score = (1.0 if has_exact else 0.0) * weights["exact_match"]
    
    answer = worker_output.get("answer", "")
    relevance_score = calculate_relevance_score(question, answer) * weights["relevance"]
    completeness_score = calculate_completeness_score(answer) * weights["completeness"]
    conciseness_score = calculate_conciseness_score(question, answer) * weights["conciseness"]
    
    total = count_score + confidence_score + exact_score + \
            relevance_score + completeness_score + conciseness_score
    
    return round(total, 4)
```

## 4. 方法论层面的优化

### 4.1 多层评估机制

```
┌─────────────────────────────────────────────┐
│              第一层：快速筛选                │
│  - 基于规则的初步过滤                        │
│  - 时效性检查                               │
└─────────────────────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────┐
│              第二层：统计评估                │
│  - 数量、置信度等统计指标                    │
│  - 基于历史数据的校准                        │
└─────────────────────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────┐
│              第三层：语义评估                │
│  - 语义相似度计算                           │
│  - 答案质量分析                             │
└─────────────────────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────┐
│              第四层：人工复核                │
│  - 低置信度结果人工审核                     │
│  - 争议结果仲裁                             │
└─────────────────────────────────────────────┘
```

### 4.2 科学加权方法

#### 方案A：熵权法（客观赋权）

```python
def calculate_entropy_weights(historical_data: List[Dict]) -> Dict[str, float]:
    """使用熵权法计算各指标权重"""
    import numpy as np
    
    metrics = ["count", "confidence", "relevance", "completeness", "conciseness"]
    data = np.array([[d[m] for m in metrics] for d in historical_data])
    
    data = (data - data.min(axis=0)) / (data.max(axis=0) - data.min(axis=0) + 1e-8)
    
    n = len(historical_data)
    p = data / data.sum(axis=0)
    p = np.where(p == 0, 1e-8, p)
    entropy = -np.sum(p * np.log(p), axis=0) / np.log(n)
    
    weights = (1 - entropy) / (len(metrics) - entropy.sum())
    
    return {m: float(w) for m, w in zip(metrics, weights)}
```

#### 方案B：置信区间估计

```python
def calculate_score_with_confidence(worker_output: WorkerOutput, 
                                query_type: str, 
                                trials: int = 100) -> Dict:
    """计算得分并给出置信区间"""
    scores = []
    for _ in range(trials):
        noise = np.random.normal(0, 0.05, 3)
        score = score_worker(worker_output, query_type)
        scores.append(min(1.0, max(0.0, score + noise.mean())))
    
    return {
        "score": np.mean(scores),
        "lower_bound": np.percentile(scores, 2.5),
        "upper_bound": np.percentile(scores, 97.5),
        "std": np.std(scores)
    }
```

## 5. 优化效果对比

| 优化方向 | 优化前 | 优化后 |
|----------|--------|--------|
| **数量指标** | 仅考虑数量 | 引入排序感知，位置加权 |
| **置信度** | 自报告，无校准 | 引入历史数据校准机制 |
| **精确匹配** | 简单布尔判断 | 语义相似度匹配 |
| **评估维度** | 3个 | 6个（可扩展） |
| **权重确定** | 人工设定 | 支持熵权法自动计算 |
| **不确定性** | 单点估计 | 置信区间估计 |

## 6. 落地建议

| 阶段 | 措施 | 预期目标 |
|------|------|----------|
| **Phase 1** | 引入语义相关性和完整性指标 | 提升评估准确性 |
| **Phase 2** | 建立历史数据收集机制 | 为校准积累数据 |
| **Phase 3** | 引入熵权法等客观赋权方法 | 减少主观权重设定 |
| **Phase 4** | 建立人工复核机制 | 形成闭环反馈 |

## 7. 与传统 RAG 评估的对比

| 维度 | 当前方案 | 传统 RAG 评估 |
|------|----------|---------------|
| 评估对象 | Worker 输出质量 | RAG 完整 pipeline |
| 指标类型 | 综合单一得分 | 多指标体系（Recall@k、NDCG等） |
| 适用场景 | 实时监控、业务决策 | 模型优化、学术研究 |
| 标准化程度 | 业务定制化 | 行业标准化 |

**结论**：本方案不可替代传统 RAG 评估指标，但可作为线上实时评估的补充方案，建议两者结合使用。

## 8. 参考资料

1. Sentence-BERT: https://www.sbert.net/
2. 熵权法原理: https://en.wikipedia.org/wiki/Entropy_weight_method
3. RAG 评估指标综述: https://arxiv.org/abs/2302.00407
