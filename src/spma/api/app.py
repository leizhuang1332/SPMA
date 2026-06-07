"""FastAPI 应用工厂。

create_app() → 注册所有路由、中间件、生命周期事件。

设计依据: API-01 端点总览
"""

from fastapi import FastAPI


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用实例。"""
    app = FastAPI(
        title="SPMA",
        version="0.1.0",
        description="企业级多源RAG智能问答系统",
    )

    @app.get("/health")
    async def health_check():
        """健康检查端点。"""
        return {"status": "ok", "version": "0.1.0"}

    return app


def main():
    """uvicorn 入口: uv run spma-api"""
    import uvicorn

    uvicorn.run("spma.api.app:create_app", host="0.0.0.0", port=8000, factory=True)
