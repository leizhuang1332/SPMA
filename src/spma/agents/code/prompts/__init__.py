"""Code agent 的 prompt 模板与响应解析模块。"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

# 同目录下 prompts.py 与 prompts/ 共存时，Python 优先解析为包。
# 用 spec_from_file_location 显式加载 prompts.py，避免被包名遮蔽。
_spec = spec_from_file_location(
    "spma.agents.code._prompts_py_legacy",
    Path(__file__).resolve().parent.parent / "prompts.py",
)
_prompts_py = module_from_spec(_spec)
_spec.loader.exec_module(_prompts_py)  # type: ignore[union-attr]

REFINE_TERMS_PROMPT = _prompts_py.REFINE_TERMS_PROMPT  # noqa: F401
del module_from_spec, spec_from_file_location, Path, _spec, _prompts_py