"""Extraction prompt accessors: resolve the configured template for the given settings."""

from __future__ import annotations

from pyro.config import Settings
from pyro.prompts import load_prompt


def extraction_system_prompt(settings: Settings) -> str:
    return load_prompt(settings.prompts.extraction_system)


def extraction_user_prompt(settings: Settings) -> str:
    return load_prompt(settings.prompts.extraction_user)


def relation_resolve_system_prompt(settings: Settings) -> str:
    return load_prompt(settings.prompts.relation_resolve_system)


def relation_resolve_user_prompt(settings: Settings) -> str:
    return load_prompt(settings.prompts.relation_resolve_user)
