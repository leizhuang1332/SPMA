# 测试 DeepSeek V4
#uv run python -c "
import asyncio
from spma.llm.providers.openai_compat import OpenAICompatProvider
from spma.llm.providers.base import ProviderConfig

async def test():
    cfg = ProviderConfig(
        type='openai_compat',
        api_key='sk-a3097afe3ab94f439c016c69d25f39ce',
        base_url='https://api.deepseek.com',
    )
    p = OpenAICompatProvider('deepseek', cfg)
    
    # 健康检查
    ok = await p.ping()
    print(f'Ping: {ok}')
    
    # 实际对话
    reply = await p.chat(
        [{'role': 'user', 'content': '你好，请介绍一下你自己'}],
        model='deepseek-v4-pro',
    )
    print(f'Reply: {reply}')

asyncio.run(test())