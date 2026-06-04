import json
import re
from pathlib import Path
from threading import Lock

import yaml
from pydantic import BaseModel

from shared.config.log_config import log

BUILTIN_SKILLS_DIR = Path(__file__).parent
_SKILL_CACHE_LOCK = Lock()


class Skill(BaseModel):
    name: str
    metadata: dict
    content: str
    path: Path


class SkillLoader:
    _skills_cache: list[Skill] | None = None

    def load_all_skills(self, ) -> list[Skill] | None:
        if self._skills_cache is not None:
            return list(self._skills_cache)

        with _SKILL_CACHE_LOCK:
            if self._skills_cache is not None:
                return list(self._skills_cache)

        count = 0
        skills = []
        for skill_file in sorted(BUILTIN_SKILLS_DIR.rglob("SKILL.md")):
            skill = None
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
            except Exception as e:
                log.error(f"加载 skill 失败 {skill_file}: {e}", exc_info=True)
                return None

        with _SKILL_CACHE_LOCK:
            self._skills_cache = list(skills)
        return skills

    def load_skill(self, name: str) -> Skill | None:
        normalized_name = str(name or "").strip().lower()
        if not normalized_name:
            return None

        skills = self.load_all_skills() or []
        for skill in skills:
            if skill.name.strip().lower() == normalized_name:
                return skill
        return None

    def load_full_text(self, name: str) -> str:
        skill = self.load_skill(name)
        if skill is None:
            raise ValueError(f"Unknown skill: {name}")
        return skill.content

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

    async def build_skill(self) -> str:
        # tool — 使用 tool_executor 的工具列表（子任务时会过滤掉 task 等工具，保持和执行器一致）
        if self._skills_cache is None:
            return ""
        skill_list = [
            {
                "name": skill.name,
                "description": str(skill.metadata.get("description", "") or "").strip(),
            }
            for skill in self._skills_cache
            if getattr(skill, "name", None)
        ]
        return json.dumps(skill_list, ensure_ascii=False, indent=2)



if __name__ == '__main__':
    skills = SkillLoader().load_all_skills()
    print(skills)