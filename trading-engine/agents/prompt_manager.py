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

    _REQUIRED_HEADERS = [
        "## Métricas del período",
        "## Setups que funcionaron",
        "## Patrones a evitar",
        "## Contexto de mercado actual",
        "## Régimen esperado próximas 24h",
        "## Reglas específicas",
        "## Cambios vs playbook anterior",
        "## Limitaciones del análisis",
    ]
    _VALID_REGIMES = {
        "TRENDING_UP", "TRENDING_DOWN", "RANGE", "HIGH_VOLATILITY", "NEUTRAL",
    }

    @classmethod
    def parse_regime_from_playbook(cls, content: str) -> str | None:
        """Extract the primary regime declared under '## Régimen esperado próximas 24h'.

        Supports both single-regime (TRENDING_UP) and distribution format
        (TRENDING_UP (60%) | RANGE (30%)). Returns the first/primary regime token.
        None when the section is missing or unparseable.
        """
        regime_header = "## Régimen esperado próximas 24h"
        if regime_header not in content:
            return None
        after = content.split(regime_header, 1)[1].strip()
        first_line = after.split("\n")[0].strip()
        import re as _re
        token_match = _re.match(r"([A-Z_]+)", first_line)
        if not token_match:
            return None
        regime_value = token_match.group(1)
        return regime_value if regime_value in cls._VALID_REGIMES else None

    @classmethod
    def parse_regime_distribution_from_playbook(cls, content: str) -> dict[str, float] | None:
        """Extract full regime distribution from playbook.

        Parses format: TRENDING_UP (60%) | RANGE (30%) | TRENDING_DOWN (10%)
        Returns dict like {"TRENDING_UP": 0.6, "RANGE": 0.3, "TRENDING_DOWN": 0.1}
        Falls back to a single-regime 100% dict if no distribution is found.
        Returns None if the section is missing.
        """
        regime_header = "## Régimen esperado próximas 24h"
        if regime_header not in content:
            return None
        after = content.split(regime_header, 1)[1].strip()
        first_line = after.split("\n")[0].strip()
        import re as _re
        parts = first_line.split("|")
        if len(parts) <= 1:
            token_match = _re.match(r"([A-Z_]+)", first_line)
            if token_match and token_match.group(1) in cls._VALID_REGIMES:
                return {token_match.group(1): 1.0}
            return None
        distribution: dict[str, float] = {}
        for part in parts:
            match = _re.match(r"\s*([A-Z_]+)\s*\((\d+)%\)", part.strip())
            if match and match.group(1) in cls._VALID_REGIMES:
                distribution[match.group(1)] = int(match.group(2)) / 100.0
        return distribution if distribution else None

    @classmethod
    def validate_playbook_markdown(cls, content: str) -> list[str]:
        """Returns a list of validation errors. Empty list means the playbook is valid."""
        errors: list[str] = []
        for header in cls._REQUIRED_HEADERS:
            if header not in content:
                errors.append(f"Sección faltante: '{header}'")

        regime_header = "## Régimen esperado próximas 24h"
        if regime_header in content:
            after = content.split(regime_header, 1)[1].strip()
            first_line = after.split("\n")[0].strip()
            import re as _re
            token_match = _re.match(r"([A-Z_]+)", first_line)
            regime_value = token_match.group(1) if token_match else first_line
            if regime_value not in cls._VALID_REGIMES:
                errors.append(
                    f"Valor inválido en '{regime_header}': '{regime_value}'. "
                    f"Esperado uno de: {', '.join(sorted(cls._VALID_REGIMES))}"
                )

        if not content.strip().startswith("# Playbook"):
            errors.append("El playbook no comienza con '# Playbook'")

        return errors

    async def save_playbook(self, *, content: str, model: str, trades_analyzed: int,
                             win_rate: float, pnl_summary: dict[str, Any] | None = None) -> PlaybookVersion:
        if self.session is None:
            raise RuntimeError("session required")

        validation_errors = self.validate_playbook_markdown(content)
        if validation_errors:
            import structlog
            structlog.get_logger().warning(
                "supervisor.playbook_validation_failed",
                errors=validation_errors,
                model=model,
            )

        latest = (await self.session.execute(
            select(PlaybookVersion).order_by(PlaybookVersion.version.desc()).limit(1)
        )).scalar_one_or_none()
        next_version = (latest.version + 1) if latest else 0
        await self.session.execute(
            update(PlaybookVersion).where(PlaybookVersion.active.is_(True)).values(active=False)
        )
        summary = pnl_summary or {}
        if validation_errors:
            summary = {**summary, "validation_errors": validation_errors}
        new = PlaybookVersion(version=next_version, content=content, model=model,
                               trades_analyzed=trades_analyzed, win_rate=Decimal(str(win_rate)),
                               pnl_summary=summary, active=True)
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
