# API 契约：Synthesis Agent

> 所属项目：[SPMA 全局概览](../SPMA-design-00-global-overview.md)
> 权威架构：[5独立Agent架构设计](../SPMA-design-07-agent-architecture.md)
> 契约边界：**Supervisor → Synthesis Agent → 最终用户响应**
> 版本：1.0

---

## 一、收敛参数

| 参数 | 值 | 说明 |
|------|-----|------|
| `max_rounds` | ≤2 | 最大审计轮数 |
| `timeout_ms` | 2000 | 超时（含执行） |
| `convergence` | `citation_coverage >= 0.8 AND contradictions.length == 0` | 收敛条件 |
| `超时策略` | 返回初稿 + 标注 | |

---

## 二、输入契约：SynthesisDispatch

```python
from pydantic import BaseModel, Field
from typing import Literal, Optional
from uuid import UUID

class SynthesisDispatch(BaseModel):
    """Supervisor → Synthesis Agent 的任务派发"""
    
    # ── 任务标识 ──
    task_id: str = Field(...)
    query_id: UUID = Field(...)
    
    # ── 输入数据 ──
    original_query: str = Field(..., description="用户原始问题")
    worker_outputs: list[WorkerOutput] = Field(..., min_length=1)
    classification: ClassificationResult = Field(...)
    
    # ── 收敛约束 ──
    max_rounds: int = Field(default=2, ge=1, le=3)
    timeout_ms: int = Field(default=2000, ge=500, le=5000)
    token_budget: int = Field(default=4, ge=1, le=8)
    
    # ── 质量要求 ──
    min_citation_coverage: float = Field(default=0.8, ge=0, le=1)
    require_cross_source_check: bool = True
    
    # ── 降级上下文 ──
    degradation_level: str = "L0"
    degraded_sources: list[str] = Field(default_factory=list)
```

---

## 三、状态模型

```python
class SynthesisAgentState(AgentState, total=False):
    """Synthesis Agent 专属状态"""
    
    # ── 输入 ──
    original_query: str
    worker_outputs: list                          # List[WorkerOutput]
    
    # ── Round 1: 生成 ──
    draft_answer: str                             # 初稿（Markdown）
    fused_citations: list[FusedCitation]           # RRF 融合后的引用列表
    rrf_params: RRFParams                         # RRF 参数快照
    
    # ── Round 2: 审计 ──
    citation_coverage: float                      # 引用覆盖率
    unverified_citations: list[UnverifiedCitation]
    contradictions: list[CrossSourceContradiction]
    coverage_gaps: list[str]                       # 用户问题中未被回答的部分
    
    # ── 完备度 ──
    assessment: str                               # "sufficient" | "insufficient: X citations unverified"
    convergence_reason: str
    
    # ── 最终输出 ──
    final_answer: str
    final_citations: list[Citation]
    audit_trail: str                              # 自检过程简述
    
    # ── 约束 ──
    max_rounds: int
    timeout_ms: int
    token_budget: int

class FusedCitation(TypedDict):
    """RRF 融合后的引用"""
    source_type: str                              # "prd" | "code" | "sql"
    source_id: str
    snippet: str
    rrf_score: float
    worker_confidence: float
    source_rankings: dict[str, int]               # 各 Worker 中的排名

class RRFParams(TypedDict):
    k: int                                        # 平滑常数，默认 60
    weights: dict[str, float]                     # 各 Worker 的融合权重

class UnverifiedCitation(TypedDict):
    source_id: str
    reason: str                                   # 无法验证的原因
    impact: str                                   # "low" | "medium" | "high"

class CrossSourceContradiction(TypedDict):
    """跨源矛盾"""
    claim: str                                    # 矛盾的陈述
    source_a: str                                 # 源 A 的引用 ID
    source_a_claim: str
    source_b: str
    source_b_claim: str
    resolution: str                               # "resolved" | "unresolved" | "flagged"
```

---

## 四、执行动作枚举

```python
class SynthesisAgentAction(StrEnum):
    RRF_FUSION = "rrf_fusion"                     # Round 1: RRF 融合排序
    LLM_GENERATE = "llm_generate"                 # Round 1: LLM 生成初稿
    CITATION_CHECK = "citation_check"             # Round 2: 引用完整性检查
    CROSS_SOURCE_CHECK = "cross_source_check"     # Round 2: 跨源一致性检查
    COVERAGE_CHECK = "coverage_check"             # Round 2: 问题覆盖度检查
    RETURN_RESULTS = "return_results"             # 返回最终结果
```

---

## 五、输出契约：SynthesisOutput

```python
class SynthesisOutput(BaseModel):
    """Synthesis Agent → Supervisor 的输出（也是最终用户响应的核心）"""
    
    $schema: str = "spma/synthesis-output/1.0"
    task_id: str
    query_id: UUID
    
    # ── 回答内容 ──
    answer: str = Field(..., description="Markdown 格式的最终回答")
    
    # ── 引用审计 ──
    citations: list[Citation] = Field(default_factory=list)
    citations_verified: int = Field(0)
    citations_unverified: int = Field(0)
    citation_coverage: float = Field(0.0, ge=0, le=1)
    
    # ── 一致性 ──
    contradictions: list[CrossSourceContradiction] = Field(default_factory=list)
    
    # ── 覆盖度 ──
    coverage_gaps: list[str] = Field(
        default_factory=list,
        description="用户问题中未被充分回答的方面"
    )
    
    # ── 执行元数据 ──
    rounds_used: int
    convergence_reason: str
    total_llm_calls: int
    total_tokens: int
    latency_ms: int
    
    # ── 审计轨迹 ──
    audit_trail: str = Field(..., description="自检过程的自然语言简述")
    
    # ── 降级与警告 ──
    degradation: Optional[DegradationInfo] = None
    warnings: list[str] = Field(default_factory=list)
    transparency_notes: list[str] = Field(
        default_factory=list,
        description="透明标注（降级策略、数据局限等）"
    )
```

---

## 六、RRF 融合算法接口

```python
def weighted_rrf_fusion(
    worker_outputs: list[WorkerOutput],
    k: int = 60,
    weights: Optional[dict[str, float]] = None
) -> list[FusedCitation]:
    """
    加权 RRF 融合——将多个 Worker 的引用结果合并排序。
    
    公式: weighted_RRF(d) = Σ w_i / (k + rank_i(d))
    
    Args:
        worker_outputs: 各 Worker 的输出（含 citations）
        k: 平滑常数（默认 60）
        weights: Worker 权重，如 {"doc": 1.0, "code": 1.0, "sql": 1.0}
        
    Returns:
        融合排序后的 FusedCitation 列表（按 rrf_score 降序，Top-20）
    """
    ...
```

---

## 七、LLM 生成 Prompt 契约

### 7.1 初稿生成 Prompt

Synthesis Agent 的 `llm_generate` 节点使用以下结构化 Prompt：

```
你是一个企业知识助手。根据以下检索结果，回答用户问题。

用户问题: {original_query}

检索结果:
{doc_results}
{code_results}
{sql_results}

要求:
1. 用 Markdown 格式组织回答，包含章节标题、列表、代码块
2. 每条陈述必须标注引用来源，格式: [源类型: 标识符]
3. 区分"确定的事实"和"推测的结论"，后者需明确标注
4. 如果跨源信息存在矛盾，显式标注矛盾点
5. 如果有未能回答的部分，在末尾列出
6. 使用中文回答
```

### 7.2 自检 Prompt（Round 2）

```
你是一个严谨的审计员。检查刚才生成的回答:

{audit_target}

检查项目:
1. 引用完整性: 每条陈述都有引用支撑吗？引用能追溯到 Worker 返回的结果吗？
2. 跨源一致性: Doc/Code/SQL 三源的信息有矛盾吗？如果有，列出来。
3. 覆盖度: 用户的原始问题 "{original_query}" 的每个方面都被回答了吗？

输出 JSON:
{
  "citation_coverage": 0.0-1.0,
  "unverified_citations": [{"source_id": "...", "reason": "...", "impact": "low|medium|high"}],
  "contradictions": [{"claim": "...", "source_a": "...", "source_a_claim": "...", "source_b": "...", "source_b_claim": "..."}],
  "coverage_gaps": ["..."],
  "verdict": "sufficient" | "insufficient: <原因>"
}
```

---

## 八、部分失败处理与透明度规则

### 8.1 Worker 部分失败的合成策略

```python
def synthesize_with_partial_results(
    original_query: str,
    worker_outputs: list[WorkerOutput],
    degraded_sources: list[str]
) -> SynthesisOutput:
    """
    当一个或多个 Worker 失败/超时时，用部分结果合成回答。
    
    策略:
    - 单源查询、目标 Agent 失败 → 标注"该数据源暂时不可用"
    - 跨源查询、1/3 Worker 失败 → 用剩余 2/3 结果 + 标注缺少的维度
    - 跨源查询、2/3 Worker 失败 → 保留成功结果 + 建议缩小查询范围
    - 全部失败 → L4 缓存兜底 + 友好错误信息
    """
    ...
```

### 8.2 透明度标注规则

Synthesis Agent 必须在输出的 `transparency_notes` 中标注以下情况：

| 场景 | 标注模板 |
|------|---------|
| 实体不足、语义搜索兜底 | "ℹ️ 本次查询未识别到具体需求ID或表名，结果基于语义搜索，可能不够精确" |
| Worker 超时 | "⏱️ [doc/code/sql] 维度的搜索超时，以下结果可能不完整" |
| 引用无法验证 | "⚠️ 以下引用无法验证: [source_id] — 请以原始数据源为准" |
| 跨源矛盾 | "⚠️ 发现信息矛盾: [Doc/Code/SQL] 说 A，但 [Doc/Code/SQL] 说 B" |
| Token 预算耗尽 | "为控制成本，搜索未完全穷尽，以下为当前最佳结果" |
| 数据质量问题 | "⚠️ 数据质量提示: [具体局限]" |

---

## 九、SynthesisOutput → 最终用户响应的映射

Supervisor 将 SynthesisAgent 的 `SynthesisOutput` 映射为 REST API 的 `QueryResponse`：

```python
def synthesis_to_api_response(
    synth_output: SynthesisOutput,
    request_id: UUID,
    agent_trace: dict
) -> dict:
    """
    将 SynthesisOutput 映射为面向用户的 REST API 响应体。
    
    SynthesisOutput          →  QueryResponse
    ────────────────────────────────────────────
    answer                   →  answer
    citations                →  citations
    contradictions/citations →  synthesis_notes
    warnings + transparency  →  metadata.degradation
    rounds_used/llm_calls    →  metadata.agent_trace
    """
    return {
        "status": _determine_status(synth_output),
        "request_id": str(request_id),
        "answer": synth_output.answer,
        "citations": [c.dict() for c in synth_output.citations],
        "synthesis_notes": {
            "citations_verified": synth_output.citations_verified,
            "citations_unverified": synth_output.citations_unverified,
            "contradictions": [
                {
                    "claim": c["claim"],
                    "resolution": c["resolution"]
                }
                for c in synth_output.contradictions
            ],
            "coverage_gaps": synth_output.coverage_gaps
        },
        "warnings": synth_output.warnings + synth_output.transparency_notes,
        "metadata": {
            "response_time_ms": synth_output.latency_ms,
            "agent_trace": agent_trace
        }
    }

def _determine_status(output: SynthesisOutput) -> str:
    if output.citations_unverified > output.citations_verified * 0.5:
        return "partial_success"
    if output.citation_coverage < 0.5:
        return "partial_success"
    if output.degradation and output.degradation.level != "L0":
        return "partial_success"
    return "success"
```
