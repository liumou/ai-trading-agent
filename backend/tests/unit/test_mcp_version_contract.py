"""
回归测试 — MCP SDK 版本契约。

背景：requirements.txt 与 Dockerfile.trading-agent 将 mcp 锁定在 1.x
（mcp>=1.0,<2）。mcp 2.x 删除了 `mcp.server.fastmcp.FastMCP`（改为 MCPServer），
导致全仓库唯一直接 mcp 导入（mcp_server/server.py:13）在运行时抛
ModuleNotFoundError，AI 分析活动事件显示 "Agent error: No module named
'mcp.server.fastmcp'"。

本测试在**不联网、不起真实 stdio 进程**的前提下，验证：
  1. 安装的 mcp 是 1.x（v1 导入路径存在）；
  2. mcp_server.server 可导入且能注册全部工具；
  3. v1 API 表面（@mcp.tool() / list_tools / call_tool）仍可工作。
若将来 mcp 漂移到 2.x 而代码未迁移，此测试立即失败。
"""

import importlib.metadata

import pytest

# 断言 mcp 主版本为 1.x —— 防止 requirements 锁被意外放宽再次引入 2.x
def test_mcp_major_version_is_1x():
    version = importlib.metadata.version("mcp")
    major = int(version.split(".")[0])
    assert major == 1, (
        f"mcp {version} installed, but backend code uses the v1 API "
        "(mcp.server.fastmcp.FastMCP). Pin mcp>=1.0,<2 in requirements.txt / "
        "Dockerfile.trading-agent, or migrate to MCPServer before allowing mcp 2.x."
    )


def test_fastmcp_import_path_exists():
    """v1 导入路径必须存在 —— 这是 AI 分析功能的硬依赖。"""
    from mcp.server.fastmcp import FastMCP  # noqa: F401

    assert FastMCP is not None


def test_mcp_server_module_imports_and_registers_tools():
    """mcp_server.server 可导入且 create_server() 能注册全部工具。"""
    from mcp_server.server import create_server

    mcp = create_server()
    # create_server() 内部注册了 40+ 工具，未抛异常即通过；
    # 再验证工具注册表非空（v1 API 的 tool 装饰器路径可用）。
    tool_manager = getattr(mcp, "_tool_manager", None)
    if tool_manager is not None:
        tools = getattr(tool_manager, "list_tools", lambda: [])()
        assert len(tools) >= 40


@pytest.mark.asyncio
async def test_v1_runtime_api_list_and_call_tool():
    """v1 运行时 API（list_tools / call_tool）在真实 FastMCP 实例上可用。

    用零参数的只读工具（get_symbols 等）避免依赖外部数据源；
    若注册表里没有可安全调用的工具则跳过执行部分。
    """
    from mcp_server.server import create_server

    mcp = create_server()
    tools = await mcp.list_tools()
    assert len(tools) >= 40
    names = {t.name for t in tools}
    # v1 的 Tool 对象暴露 inputSchema 字段（2.x 改名为 input_schema）
    sample = next((t for t in tools if hasattr(t, "inputSchema")), None)
    assert sample is not None, "v1 Tool.inputSchema attribute missing"

    # 可选执行：挑一个无需外部数据、零必填参数的工具验证 call_tool 路径
    safe_tools = names & {"get_symbols", "get_broker_mode", "list_strategies"}
    if safe_tools:
        tool = sorted(safe_tools)[0]
        result = await mcp.call_tool(tool, {})
        assert result is not None
