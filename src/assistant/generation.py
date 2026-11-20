"""Redaction : interface, faux deterministe, Gemini sur Vertex AI."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

MARQUEUR_REFUS = "[REFUS]"


@dataclass
class Fragment:
    texte: str = ""
    jetons_entree: int | None = None
    jetons_sortie: int | None = None


class Generateur(Protocol):
    nom: str

    def rediger(self, consigne: str, contenu: str, max_jetons: int) -> AsyncIterator[Fragment]: ...


class FauxGenerateur:
    """Reponse extractive : la premiere phrase utile du passage [1], citee.

    Deterministe et hors reseau. Garde la trace des appels pour les tests
    (par exemple : prouver qu'un refus sous le seuil n'appelle pas le modele).
    """

    nom = "faux-extractif-v1"

    def __init__(self, reponse_forcee: str | None = None) -> None:
        self.appels: list[dict] = []
        self.reponse_forcee = reponse_forcee

    def _texte(self, contenu: str) -> str:
        if self.reponse_forcee is not None:
            return self.reponse_forcee
        mo = re.search(r"\[1\][^\n]*\n(.+?)(?:\n\[2\]|\Z)", contenu, flags=re.S)
        if not mo:
            return MARQUEUR_REFUS
        corps = " ".join(mo.group(1).split())
        phrase = re.split(r"(?<=[.!?])\s+", corps)[0]
        return f"{phrase} [1]"

    async def rediger(
        self, consigne: str, contenu: str, max_jetons: int
    ) -> AsyncIterator[Fragment]:
        self.appels.append({"consigne": consigne, "contenu": contenu, "max_jetons": max_jetons})
        mots = self._texte(contenu).split(" ")[:max_jetons]
        for i, mot in enumerate(mots):
            yield Fragment(texte=mot if i == 0 else " " + mot)
        yield Fragment(jetons_entree=len((consigne + contenu).split()), jetons_sortie=len(mots))


class VertexGenerateur:
    """Gemini sur Vertex AI, en flux. Non execute par les tests."""

    def __init__(
        self, projet: str, region: str, modele: str, temperature: float = 0.2, reflexion: str = ""
    ) -> None:
        from google import genai  # import paresseux : dependance optionnelle

        self._client = genai.Client(vertexai=True, project=projet, location=region)
        self.nom = modele
        self.temperature = temperature
        self.reflexion = reflexion

    async def rediger(
        self, consigne: str, contenu: str, max_jetons: int
    ) -> AsyncIterator[Fragment]:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=consigne,
            max_output_tokens=max_jetons,
            temperature=self.temperature,
        )
        if self.reflexion:
            # Gemini 3 : niveau de reflexion plutot que budget. A verifier au jour 1.
            config.thinking_config = types.ThinkingConfig(thinking_level=self.reflexion)
        flux = await self._client.aio.models.generate_content_stream(
            model=self.nom, contents=contenu, config=config
        )
        usage = None
        async for morceau in flux:
            if morceau.text:
                yield Fragment(texte=morceau.text)
            if morceau.usage_metadata:
                usage = morceau.usage_metadata
        if usage:
            yield Fragment(
                jetons_entree=usage.prompt_token_count or 0,
                jetons_sortie=usage.candidates_token_count or 0,
            )
