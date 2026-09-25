"""SkillRegistry —— 全局 skill 注册表（P2 服务化，docs/SystemApp架构设计.md §4.5）。

原 SkillLoader._skills_cache 类属性（全局一份却挂在会话类上）上收为容器组件：
启动时装载一次；热更新走显式 reload()，而不是再 new loader。
"""
import json
import re
from pathlib import Path
from threading import Lock

import yaml
from pydantic import BaseModel

from shared import log

BUILTIN_SKILLS_DIR = Path(__file__).parent
_SKILL_CACHE_LOCK = Lock()


class Skill(BaseModel):
    name: str
    metadata: dict
    content: str
    path: Path


class SkillRegistry:
    """进程内唯一 skill 注册表：装载/检索/prompt 渲染。"""

    def __init__(self) -> None:
        self._skills: list[Skill] | None = None

    # ------------------------------------------------------------------ loading

    def load(self) -> list[Skill]:
        """装载全部内置 skill（幂等；已装载直接返回副本）。"""
        if self._skills is not None:
            return list(self._skills)

        with _SKILL_CACHE_LOCK:
            if self._skills is not None:
                return list(self._skills)

        count = 0
        skills = []
        for skill_file in sorted(BUILTIN_SKILLS_DIR.rglob("SKILL.md")):
            try:
                content = skill_file.read_text(encoding="utf-8")
                metadata = self._parse_metadata(content)
                if not metadata:
                    continue
                skill_content = self._parse_content(content)
                skill_name = metadata.get("name") or skill_file.parent.name
                skill = Skill(
                    name=skill_name,
                    content=skill_content,
                    metadata=metadata,
                    path=skill_file
                )
                skills.append(skill)
                count += 1
            except Exception as e:
                log.error(f"加载 skill 失败 {skill_file}: {e}", exc_info=True)
                return None  # 与原行为一致：装载失败返回 None（不缓存半成品）

        with _SKILL_CACHE_LOCK:
            self._skills = list(skills)
        return skills

    def reload(self) -> list[Skill]:
        """显式热更新（skill 文件变更后调用）。"""
        with _SKILL_CACHE_LOCK:
            self._skills = None
        return self.load()

    # ------------------------------------------------------------------ lookup

    def all(self) -> list[Skill]:
        return self.load() or []

    def get(self, name: str) -> Skill | None:
        normalized_name = str(name or "").strip().lower()
        if not normalized_name:
            return None

        for skill in self.all():
            if skill.name.strip().lower() == normalized_name:
                return skill
        return None

    def full_text(self, name: str) -> str:
        skill = self.get(name)
        if skill is None:
            raise ValueError(f"Unknown skill: {name}")
        return skill.content

    # ------------------------------------------------------------------ prompt

    def build_prompt(self, allowlist: list[str] | None = None) -> str:
        """skill 目录 prompt（P4：allowlist 过滤智能体可见范围；None=全量）。"""
        skills = self.all()
        if not skills:
            return ""
        allowed = {str(n).strip().lower() for n in (allowlist or []) if str(n).strip()}
        skill_list = [
            {
                "name": skill.name,
                "description": str(skill.metadata.get("description", "") or "").strip(),
            }
            for skill in skills
            if getattr(skill, "name", None)
            and (not allowed or skill.name.strip().lower() in allowed)
        ]
        return json.dumps(skill_list, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------ parsing

    def _parse_metadata(self, content: str) -> dict | None:
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if not match:
            return None
        try:
            return yaml.safe_load(match.group(1))
        except Exception as e:
            log.error(f"解析元数据失败: {e}")
            return None

    def _parse_content(self, content) -> str | None:
        return re.sub(r"^---\s*\n.*?\n---\s*\n", "", content, count=1, flags=re.DOTALL).strip()
