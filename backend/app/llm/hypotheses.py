from __future__ import annotations

import json
import re
from typing import Protocol

import httpx

from app.config import Settings
from app.schemas import Entity, Evidence, Hypothesis, IncidentInput


class HypothesisGenerator(Protocol):
    async def propose(
        self,
        incident: IncidentInput,
        entities: list[Entity],
        evidence: list[Evidence],
        deterministic: list[Hypothesis],
    ) -> list[Hypothesis]: ...


class NullHypothesisGenerator:
    async def propose(self, incident, entities, evidence, deterministic) -> list[Hypothesis]:
        return []


class OpenAICompatibleHypothesisGenerator:
    """Optional hypothesis expander.

    The model has no infrastructure credentials or tools. Its output is always
    mapped to PROPOSED hypotheses with zero evidence. Deterministic checks must
    validate them in a later playbook before they can affect root cause.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    async def propose(
        self,
        incident: IncidentInput,
        entities: list[Entity],
        evidence: list[Evidence],
        deterministic: list[Hypothesis],
    ) -> list[Hypothesis]:
        context = {
            "incident": incident.model_dump(mode="json"),
            "entities": [e.model_dump(mode="json") for e in entities],
            "evidence": [
                {"source": e.source, "category": e.category, "summary": e.summary, "facts": e.facts}
                for e in evidence
            ],
            "existing_hypotheses": [h.statement for h in deterministic],
        }
        system = (
            "You are an SRE hypothesis generator. Suggest only additional plausible hypotheses not already listed. "
            "Do not diagnose, do not assign confidence, and do not claim evidence. Return JSON only with shape "
            '{"hypotheses":[{"id":"short-id","statement":"...","required_checks":["..."]}]}. '
            "Each required check must be an observable read-only fact."
        )
        headers = {"Content-Type": "application/json"}
        if self.settings.llm_api_key:
            headers["Authorization"] = f"Bearer {self.settings.llm_api_key}"
        payload = {
            "model": self.settings.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(context, separators=(",", ":"), default=str)},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        try:
            async with httpx.AsyncClient(base_url=self.settings.llm_base_url, headers=headers, timeout=20) as client:
                response = await client.post("/chat/completions", json=payload)
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return []

        output: list[Hypothesis] = []
        for item in parsed.get("hypotheses", [])[: self.settings.llm_max_hypotheses]:
            statement = str(item.get("statement", "")).strip()
            if not statement:
                continue
            raw_id = re.sub(r"[^a-z0-9-]+", "-", str(item.get("id", "candidate")).lower()).strip("-")
            checks = [str(v) for v in item.get("required_checks", []) if str(v).strip()][:6]
            output.append(Hypothesis(
                id=f"llm-{raw_id or 'candidate'}",
                statement=statement,
                origin="llm",
                required_checks=checks,
                rationale="LLM-proposed only; requires a typed deterministic check before it can influence RCA.",
            ))
        return output
