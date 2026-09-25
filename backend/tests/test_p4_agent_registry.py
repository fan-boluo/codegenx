"""P4 多智能体单测：AgentSpec/AgentRegistry + 模型路由覆盖 + persona 渲染（§10）。

运行：backend/.venv/Scripts/python.exe -m pytest backend/tests/test_p4_agent_registry.py -q
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from codegenx.ai_service.agent.agent_registry import AgentRegistry, AgentSpec, MemoryPolicy
from codegenx.ai_service.context.context_service import ContextService, TurnPrompts
from codegenx.ai_service.utils.config import AgentConfig, Config


# ── AgentRegistry 存取与校验 ────────────────────────────────────────────────

def _spec(name: str, **kw) -> AgentSpec:
    kw.setdefault("persona", f"你是{name}")
    return AgentSpec(name=name, **kw)


def test_registry_get_falls_back_to_default():
    reg = AgentRegistry([_spec("planner")])
    assert reg.get(None) is reg.default()
    assert reg.get("unknown_agent") is reg.default()  # 未知名回落 default（现行为）
    assert reg.get("PLANNER").name == "planner"       # 大小写不敏感


def test_registry_duplicate_name_rejected():
    with pytest.raises(ValueError, match="重复"):
        AgentRegistry([_spec("planner"), _spec("planner")])


def test_registry_validate_against_rejects_unknown_tool_and_skill():
    reg = AgentRegistry([_spec("p", tools=["read_file"], skills=["ok-skill"])])
    reg.validate_against({"read_file"}, {"ok-skill"})  # 通过

    bad_tool = AgentRegistry([_spec("p", tools=["no_such_tool"])])
    with pytest.raises(ValueError, match="不存在的工具"):
        bad_tool.validate_against({"read_file"}, set())

    bad_skill = AgentRegistry([_spec("p", skills=["no-such-skill"])])
    with pytest.raises(ValueError, match="不存在的 skill"):
        bad_skill.validate_against(set(), {"ok-skill"})


# ── config.agents → AgentSpec 装配（含 camelCase 兼容） ─────────────────────

def _cfg_with_agents(*agents: dict) -> Config:
    return Config(agents=[AgentConfig(**a) for a in agents])


def test_from_config_maps_spec_fields():
    cfg = _cfg_with_agents(
        {
            "id": "planner", "defaults": True,
            "persona": "规划师",
            "tools": ["read_file"], "skills": ["planning"],
            "modelOverride": {"agent_main": ["m1", "m2"]},
            "memory": {"writeEnabled": True, "readTypes": ["hard_constraint"]},
        },
        {"id": "data_explore", "persona": "探查员",
         "memory": {"writeEnabled": False}},
    )
    reg = AgentRegistry.from_config(cfg)

    # defaults=True → 默认 spec
    assert reg.get(None).name == "planner"
    planner = reg.get("planner")
    assert planner.persona == "规划师"
    assert planner.tools == ["read_file"]
    assert planner.model_override == {"agent_main": ["m1", "m2"]}
    assert planner.memory.read_types == ["hard_constraint"]
    assert planner.memory.write_enabled is True

    # 纯执行智能体关写（防污染）
    explorer = reg.get("data_explore")
    assert explorer.memory.write_enabled is False
    assert explorer.memory.read_types is None


def test_from_config_non_default_persona_required():
    cfg = _cfg_with_agents({"id": "a", "defaults": True}, {"id": "b"})
    with pytest.raises(ValueError, match="persona"):
        AgentRegistry.from_config(cfg)


def test_from_config_empty_agents_single_agent_equivalent():
    """不配置 agents 段 = 仅默认 spec，与现单智能体行为完全等价。"""
    reg = AgentRegistry.from_config(Config())
    default = reg.default()
    assert default.persona == ""
    assert default.tools is None and default.skills is None
    assert default.model_override is None
    assert default.memory.write_enabled is True


# ── 模型路由：智能体维度覆盖（§10.4） ───────────────────────────────────────

def test_model_chain_agent_override_highest_priority():
    cfg = Config(
        model_roles={"agent_main": ["s1"], "data_explore:agent_main": ["g1", "g2"]},
    )
    override = {"agent_main": "qwen-x"}
    chain = cfg.get_model_chain("agent_main", agent="data_explore", agent_override=override)
    assert chain == ["qwen-x"]  # spec.model_override > model_roles["{agent}:{scenario}"]


def test_model_chain_agent_scenario_role_second():
    cfg = Config(model_roles={"agent_main": ["s1"], "data_explore:agent_main": ["g1", "g2"]})
    chain = cfg.get_model_chain("agent_main", agent="data_explore")
    assert chain == ["g1", "g2"]  # 整链替换，primary_override 不叠加


def test_model_chain_scenario_fallback_unchanged():
    """无智能体覆盖 → 现有行为完全不变（scenario 链 + primary_override 换头）。"""
    cfg = Config(model_roles={"agent_main": ["s1", "s2"]})
    assert cfg.get_model_chain("agent_main", primary_override="m0") == ["m0", "s2"]
    assert cfg.get_model_chain("agent_main") == ["s1", "s2"]
    # 未配置场景 → [默认模型]
    assert cfg.get_model_chain("no_such_scenario") == [cfg.get_default_model()]


# ── persona 渲染（§10.3） ──────────────────────────────────────────────────

def test_render_turn_context_uses_persona():
    svc = ContextService()
    prompts = TurnPrompts(workspace_metadata={
        "code_dir": "/tmp/x", "safe_paths": [], "allowed_rw_dirs": [],
        "os_name": "windows", "project_skeleton": "", "db_name": "",
        "csv_data_dirs": [], "timestamp": "t",
    })
    out = svc.render_turn_context(prompts, persona="你是数据探查员，工作目录 {code_dir}。")
    assert "你是数据探查员" in out
    # 空 persona → 内置默认模板（现行为不变）
    out_default = svc.render_turn_context(TurnPrompts(workspace_metadata={}))
    assert out_default == ""


# ── SubagentContext 扩展 ───────────────────────────────────────────────────

def test_subagent_context_has_agent_name():
    from codegenx.ai_service.agent.subagent_runner import SubagentContext

    ctx = SubagentContext(prompt="x")
    assert ctx.agent_name == ""  # 空=默认智能体
