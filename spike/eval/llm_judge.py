"""调用 LLM API 做完备度判断。"""

import json
import re
from pathlib import Path
from typing import Any

import anthropic
import yaml


class LLMJudge:
    """LLM 完备度判断器 —— 加载 Prompt 模板，调用 Haiku API，返回判断结果。"""

    def __init__(self, config_path: str | Path = "config.yaml") -> None:
        with open(config_path, encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        api_config = self.config["api"]
        self.client = anthropic.Anthropic()
        self.model = api_config["model"]
        self.max_tokens = api_config["max_tokens"]
        self.temperature = api_config["temperature"]

    def load_prompt(self, version: str) -> str:
        """加载 Prompt 模板。

        Args:
            version: "A" | "B" | "C" | "final"

        Returns:
            Prompt 模板文本
        """
        filename_map = {
            "A": "prompt_a_simple.md",
            "B": "prompt_b_structured.md",
            "C": "prompt_c_scoring.md",
            "final": "prompt_final.md",
        }
        filename = filename_map.get(version)
        if filename is None:
            raise ValueError(f"未知 Prompt 版本: {version}，可选: A/B/C/final")

        prompt_path = Path(self.config["paths"]["prompts_dir"]) / filename
        if not prompt_path.exists():
            raise FileNotFoundError(f"Prompt 文件不存在: {prompt_path}")

        return prompt_path.read_text(encoding="utf-8")

    def judge(self, query_text: str, results: list[dict], prompt_version: str) -> dict:
        """调用 LLM 做完备度判断。

        Args:
            query_text: 用户问题
            results: 本轮检索结果列表
            prompt_version: Prompt 版本 "A" | "B" | "C" | "final"

        Returns:
            {
                "verdict": "sufficient" | "insufficient",
                "confidence": float,
                "raw_response": str,
                "parsed_json": dict | None,
            }
        """
        template = self.load_prompt(prompt_version)
        results_str = json.dumps(results, ensure_ascii=False, indent=2)
        prompt = template.replace("{query}", query_text).replace("{results_json}", results_str)

        message = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            messages=[{"role": "user", "content": prompt}],
        )

        raw_response = message.content[0].text

        if prompt_version == "A":
            return self._parse_simple(raw_response)
        else:
            return self._parse_structured(raw_response)

    def _parse_simple(self, response: str) -> dict:
        """解析 Prompt A 的简单回答。"""
        is_sufficient = "够" in response and "不够" not in response
        return {
            "verdict": "sufficient" if is_sufficient else "insufficient",
            "confidence": 0.5,
            "raw_response": response,
            "parsed_json": None,
        }

    def _parse_structured(self, response: str) -> dict:
        """解析 Prompt B/C 的 JSON 输出。"""
        parsed = self._extract_json(response)
        verdict = parsed.get("verdict", "insufficient") if parsed else "insufficient"
        confidence = parsed.get("confidence", 0.5) if parsed else 0.5
        return {
            "verdict": verdict,
            "confidence": confidence,
            "raw_response": response,
            "parsed_json": parsed,
        }

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        """从 LLM 响应中提取 JSON 对象。"""
        match = re.search(r"\{[^{}]*\{[^{}]*\}[^{}]*\}|\{[^{}]*\}", text, re.DOTALL)
        if not match:
            # 尝试匹配 ```json ... ``` 包裹的块
            match = re.search(r"```json\s*([\s\S]*?)\s*```", text)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    return None
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

    def judge_batch(
        self, points: list[dict], prompt_version: str
    ) -> list[dict]:
        """批量判断。

        Args:
            points: 判断点列表
            prompt_version: Prompt 版本

        Returns:
            每个判断点附加 "llm_verdict", "llm_confidence", "llm_raw_response"
        """
        results = []
        for point in points:
            result = self.judge(
                point["query_text"], point["results"], prompt_version
            )
            point_copy = dict(point)
            point_copy["llm_verdict"] = result["verdict"]
            point_copy["llm_confidence"] = result["confidence"]
            point_copy["llm_raw_response"] = result["raw_response"]
            point_copy["llm_parsed"] = result.get("parsed_json")
            results.append(point_copy)
        return results
