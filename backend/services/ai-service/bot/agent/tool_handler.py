import asyncio
import importlib
import sys
from abc import ABC
from pathlib import Path
from typing import Any, Callable
from pydantic import BaseModel
from bot.tools.base import Tool, BaseTool
from bot.tools.file import WriteFileTool, ReadFileTool
from bot.utils.log_utils import log

# 工具目录
BUILTIN_TOOLS_DIR = Path(__file__).parent.parent / "tools"

class ToolsHandler:
    def __init__(self):
        self.tools: list[Tool] = []
        self.load_tools()

    def load_tools(self):
        """
        加载所有的工具
        """
        classes = []
        pattern = "*.py"

        # 遍历所有 .py 文件
        for file_path in BUILTIN_TOOLS_DIR.glob(pattern):
            if file_path.name.startswith("_"):
                continue  # 跳过 __init__.py 等
            # 拼接模块名
            relative_path = file_path.relative_to(BUILTIN_TOOLS_DIR)
            module_name = relative_path.with_suffix("").as_posix().replace("/", ".")
            try:
                # 导入模块
                module = importlib.import_module(f"bot.tools.{module_name}")
                # log.debug(f"导入模块成功: {module_name}")
                # 遍历模块里所有成员
                for name, obj in vars(module).items():
                    # 筛选：是类 + 继承自 base_class + 不是自己
                    if (
                            isinstance(obj, type)  # 是类
                            and issubclass(obj, BaseTool)  # 继承 BaseTool
                            and obj != BaseTool  # 排除基类自身
                            and not isinstance(obj, ABC)  # 不是抽象类
                    ):
                        classes.append(obj)
                        log.debug(f"✅ 找到工具类: {module_name}.{name}")

            except Exception as e:
                log.error(f"导入失败 {file_path}: {e}")
        for tool_cls in classes:
            try :
                tool_instance = tool_cls()
                name = getattr(tool_instance, "name", None)
                label = getattr(tool_instance, "label", None)
                description = getattr(tool_instance, "description", None)
                parameters = getattr(tool_instance, "parameters", None)
                if not all([name, description, parameters]):
                    log.warning(f"⚠️ 工具 {tool_cls.__name__} 缺少必填字段，跳过")
                    continue
                tool = Tool(
                    name=name,
                    label=label,
                    description=description,
                    parameters=parameters,
                    executor=tool_instance.execute
                )
                existing_tool = next((t for t in self.tools if t.name == name), None)
                if existing_tool is None:
                    self.tools.append(tool)
                    log.info(f"🚀 注册工具成功: {name}")
                else:
                    log.info(f"工具已注册跳过: {name}")

            except Exception as e:
                log.error(f"❌ 初始化工具失败 {tool_cls.__name__}: {str(e)}")

        log.info(f"\n🎉 工具加载完成，总计加载: {len(self.tools)} 个工具")


    def call_tool(self, tool_name: str, tool_input: dict) -> Any:
        """
        标准化工具调用函数
        """
        func = None
        for tool in self.tools:
            if tool_name == tool.name:
                func = tool.executor
        if func:
            log.info(f"执行工具：{tool_name}")
            return func(**tool_input)
        else:
            return {"error": f"未知工具：{tool_name}"}


    def tool_prompt(self):
        """ TODO 发送给llm时的tool的信息 """
        pass


if __name__ == '__main__':
    write_params = {"path": "test_write_tool.txt",
              "content": "aaaaaaaaaaaaa\naj急急急安51234rf!@#$%^^&&*(())_"
              }


    async def test_write():
        write_tool = WriteFileTool()
        write_tool.validate_params(write_params)
        result = await write_tool.execute(write_params)
        print(result)


    read_params = {"path": "test_write_tool.txt",
                   }
    async def test_read():
        tool = ReadFileTool()
        tool.validate_params(read_params)
        result = await tool.execute(read_params)
        print(result)

    # asyncio.run(test_read())

    handler = ToolsHandler()
    handler.load_tools()  # 二次执行