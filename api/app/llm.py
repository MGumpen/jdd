"""Tynn innpakning rundt Anthropic Messages API.

Brukes bare i KI-modus (når ANTHROPIC_API_KEY er satt). Alle kall har en
regelbasert reserve i modulen som kaller hit, så prototypen virker uten nøkkel.
"""

import json

import anthropic

from . import config


class LLMError(Exception):
    """Kalles når modellen ikke gir et brukbart svar."""


_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        # Korte tidsavbrudd: dette er en demo, og workeren skal ikke henge.
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=60.0, max_retries=2)
    return _client


def complete(system: str, prompt: str, max_tokens: int = 8000) -> str:
    """Sender én melding og returnerer svaret som tekst."""
    response = _get_client().messages.create(
        model=config.LLM_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        # Enkle oppgaver: lav innsats gir raskere og billigere svar.
        output_config={"effort": "low"},
    )
    return _text_of(response)


def complete_json(system: str, prompt: str, schema: dict, max_tokens: int = 8000) -> dict:
    """Som complete(), men tvinger svaret til å følge et JSON-skjema (structured outputs)."""
    response = _get_client().messages.create(
        model=config.LLM_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
    )
    try:
        return json.loads(_text_of(response))
    except json.JSONDecodeError as exc:
        raise LLMError(f"Ugyldig JSON fra modellen: {exc}") from exc


def _text_of(response) -> str:
    if response.stop_reason == "refusal":
        raise LLMError("Modellen avslo forespørselen")
    if response.stop_reason == "max_tokens":
        raise LLMError("Svaret ble avkortet (max_tokens)")
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        raise LLMError("Tomt svar fra modellen")
    return text
