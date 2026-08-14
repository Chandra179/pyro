"""Synthesis prompt accessors: resolve the configured template for the given settings."""

from __future__ import annotations

from pyro.config import Settings
from pyro.prompts import load_prompt


def synthesis_system_prompt(settings: Settings) -> str:
    return load_prompt(settings.prompts.synthesis_system)


def synthesis_user_prompt(settings: Settings) -> str:
    return load_prompt(settings.prompts.synthesis_user)


def batch_synthesis_system_prompt(settings: Settings) -> str:
    return load_prompt(settings.prompts.synthesis_batch_system)


def batch_synthesis_user_prompt(settings: Settings) -> str:
    return load_prompt(settings.prompts.synthesis_batch_user)


def freeform_route_system_prompt(settings: Settings) -> str:
    return load_prompt(settings.prompts.synthesis_freeform_route_system)


def freeform_route_user_prompt(settings: Settings) -> str:
    return load_prompt(settings.prompts.synthesis_freeform_route_user)
