"""Provider configuration for the inspection Agent and its replay."""

import json
import os
from contextlib import asynccontextmanager

from agents import OpenAIChatCompletionsModel
from openai import AsyncOpenAI


DEFAULT_MODEL = "gpt-4.1-mini"
PROVIDERS = ("openai", "deepseek")


def model_name(provider, override):
    default = DEFAULT_MODEL if provider == "openai" else "deepseek-flash"
    return override or os.getenv(f"{provider.upper()}_MODEL") or default


@asynccontextmanager
async def configured_model(provider, name):
    key_name = f"{provider.upper()}_API_KEY"
    key = os.getenv(key_name)
    if not key:
        raise ValueError(f"当前环境缺少 {key_name}。")
    if provider == "openai":
        yield name
        return

    async with AsyncOpenAI(api_key=key, base_url="https://api.deepseek.com", timeout=25) as client:
        create = client.chat.completions.create

        async def deepseek_create(**kwargs):
            response_format = kwargs.get("response_format")
            if isinstance(response_format, dict) and response_format.get("type") == "json_schema":
                schema = response_format["json_schema"]["schema"]
                kwargs["response_format"] = {"type": "json_object"}
                kwargs["messages"][0]["content"] += (
                    "\n完成工具调用后，最终输出必须是符合下列 JSON Schema 的 JSON 对象：\n"
                    + json.dumps(schema, ensure_ascii=False)
                )
            kwargs.pop("parallel_tool_calls", None)
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
            if isinstance(kwargs.get("tools"), list):
                for tool in kwargs["tools"]:
                    tool.get("function", {}).pop("strict", None)
            return await create(**kwargs)

        client.chat.completions.create = deepseek_create
        yield OpenAIChatCompletionsModel(model=name, openai_client=client)
