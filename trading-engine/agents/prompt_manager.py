from __future__ import annotations
from decimal import Decimal
from pathlib import Path
from typing import Any
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db.models import PlaybookVersion

PROMPTS_DIR = Path(__file__).parent / "prompts"


class PromptManager:
    def __init__(self, session: AsyncSession | None):
        self.session = session

    def load_system_prompt(self, agent: str) -> str:
        return (PROMPTS_DIR / f"{agent}_system.txt").read_text(encoding="utf-8")

    def load_user_template(self, agent: str) -> str:
        return (PROMPTS_DIR / f"{agent}_user.txt").read_text(encoding="utf-8")

    def render_user_prompt(self, agent: str, values: dict[str, Any], *, strict: bool = True) -> str:
        template = self.load_user_template(agent)
        if strict:
            return template.format_map(values)
        return template.format_map(_DefaultDict(values))

    async def seed_playbook_v0(self) -> None:
        if self.session is None:
            raise RuntimeError("session required")
        existing = (await self.session.execute(select(PlaybookVersion))).scalars().first()
        if existing is not None:
            return
        content = (PROMPTS_DIR / "playbook_v0.md").read_text(encoding="utf-8")
        self.session.add(PlaybookVersion(version=0, content=content, model="bootstrap", active=True))
        await self.session.commit()

    async def get_active_playbook(self) -> PlaybookVersion | None:
        if self.session is None:
            raise RuntimeError("session required")
        return (await self.session.execute(
            select(PlaybookVersion).where(PlaybookVersion.active.is_(True))
        )).scalar_one_or_none()

    async def save_playbook(self, *, content: str, model: str, trades_analyzed: int,
                             win_rate: float, pnl_summary: dict[str, Any] | None = None) -> PlaybookVersion:
        if self.session is None:
            raise RuntimeError("session required")
        latest = (await self.session.execute(
            select(PlaybookVersion).order_by(PlaybookVersion.version.desc()).limit(1)
        )).scalar_one_or_none()
        next_version = (latest.version + 1) if latest else 0
        await self.session.execute(
            update(PlaybookVersion).where(PlaybookVersion.active.is_(True)).values(active=False)
        )
        new = PlaybookVersion(version=next_version, content=content, model=model,
                               trades_analyzed=trades_analyzed, win_rate=Decimal(str(win_rate)),
                               pnl_summary=pnl_summary or {}, active=True)
        self.session.add(new)
        await self.session.commit()
        await self.session.refresh(new)
        return new


class _Missing:
    """Sentinel for a missing context key — renders as {key} or {key:spec} in the output."""
    def __init__(self, key: str) -> None:
        self._key = key

    def __format__(self, spec: str) -> str:
        if spec:
            return "{" + self._key + ":" + spec + "}"
        return "{" + self._key + "}"

    def __str__(self) -> str:
        return "{" + self._key + "}"


class _DefaultDict(dict):
    def __missing__(self, key: str) -> "_Missing":
        return _Missing(key)
