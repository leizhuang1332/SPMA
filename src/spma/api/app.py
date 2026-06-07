"""FastAPI 应用工厂。

create_app() → 注册所有路由、中间件、生命周期事件。

设计依据: API-01 端点总览
"""

from fastapi import FastAPI


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用实例。"""
    raise NotImplementedError


def main():
    """uvicorn 入口: uv run spma-api"""
    raise NotImplementedError
