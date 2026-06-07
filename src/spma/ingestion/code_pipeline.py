"""代码仓库摄入主流程。

Git Webhook → git pull → git ls-files → upsert file_path_cache
→ TreeSitter 解析变更文件 AST → upsert code_metadata

注意: 不存储源代码——Code Agent 通过 read_file 实时读取。

设计依据: SPMA-design-05 §2 代码摄入管道
"""
