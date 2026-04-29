import re
from pathlib import Path

import yaml
from pydantic import BaseModel

from bot.utils.log_utils import log

BUILTIN_SKILLS_DIR = Path(__file__).parent


class Skill(BaseModel):
    name: str
    metadata: dict
    content: str
    path: Path


class SkillLoader:

    def load_all_skills(self, ) -> list[Skill] | None:
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


if __name__ == '__main__':
    skills = SkillLoader().load_all_skills()
    print(skills)