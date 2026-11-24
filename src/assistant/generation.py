"""Redaction : interface, faux deterministe, Gemini sur Vertex AI."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

MARQUEUR_REFUS = "[REFUS]"


@dataclass
class Fragment:
    texte: str = ""
    jetons_entree: int | None = None  # entree facturee, cache compris
    jetons_sortie: int | None = None  # reponse, sans la reflexion
    jetons_cache: int | None = None  # part de l'entree lue dans le cache implicite
    jetons_reflexion: int | None = None  # facturee au prix de la sortie


class Generateur(Protocol):
    nom: str

    def rediger(
        self, consigne: str, contenu: str, max_jetons: int, schema: dict | None = None
    ) -> AsyncIterator[Fragment]: ...


def usage_vertex(usage) -> Fragment:
    """Compteurs de jetons de google-genai. Gemini compte la reflexion a part
    (`thoughts_token_count`) et la facture au prix de la sortie ; l'entree lue
    dans le cache (`cached_content_token_count`) est comprise dans `prompt_token_count`."""
    return Fragment(
        jetons_entree=getattr(usage, "prompt_token_count", None) or 0,
        jetons_sortie=getattr(usage, "candidates_token_count", None) or 0,
        jetons_cache=getattr(usage, "cached_content_token_count", None) or 0,
        jetons_reflexion=getattr(usage, "thoughts_token_count", None) or 0,
    )


class FauxGenerateur:
    """Reponse extractive : la premiere phrase utile du passage [1], citee.

    Deterministe et hors reseau. Garde la trace des appels pour les tests
    (par exemple : prouver qu'un refus sous le seuil n'appelle pas le modele).
    En sortie structuree (schema donne), rend l'objet JSON de l'etiquette en
    fragments de quelques caracteres, comme le flux de Gemini.
    """

    nom = "faux-extractif-v1"

    def __init__(
        self, reponse_forcee: str | None = None, jetons_cache: int = 0, jetons_reflexion: int = 0
    ) -> None:
        self.appels: list[dict] = []
        self.reponse_forcee = reponse_forcee
        self.jetons_cache = jetons_cache
        self.jetons_reflexion = jetons_reflexion

    def _texte(self, contenu: str) -> str:
        if self.reponse_forcee is not None:
            return self.reponse_forcee
        mo = re.search(r"\[1\][^\n]*\n(.+?)(?:\n\[2\]|\n</passages>|\Z)", contenu, flags=re.S)
        if not mo:
            return MARQUEUR_REFUS
        corps = " ".join(mo.group(1).split())
        phrase = re.split(r"(?<=[.!?])\s+", corps)[0]
        return f"{phrase} [1]"

    async def rediger(
        self, consigne: str, contenu: str, max_jetons: int, schema: dict | None = None
    ) -> AsyncIterator[Fragment]:
        self.appels.append(
            {"consigne": consigne, "contenu": contenu, "max_jetons": max_jetons, "schema": schema}
        )
        texte = self._texte(contenu)
        if schema is not None:
            if self.reponse_forcee is None:
                objet = (
                    {"intention": "hors_sujet", "reponse": ""}
                    if texte == MARQUEUR_REFUS
                    else {"intention": "question", "reponse": texte}
                )
                texte = json.dumps(objet, ensure_ascii=False)
            morceaux = [texte[i : i + 7] for i in range(0, len(texte), 7)]
            sortie = len(texte.split())
        else:
            mots = texte.split(" ")[:max_jetons]
            morceaux = [mot if i == 0 else " " + mot for i, mot in enumerate(mots)]
            sortie = len(mots)
        for m in morceaux:
            yield Fragment(texte=m)
        yield Fragment(
            jetons_entree=len((consigne + contenu).split()),
            jetons_sortie=sortie,
            jetons_cache=self.jetons_cache,
            jetons_reflexion=self.jetons_reflexion,
        )


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
        self, consigne: str, contenu: str, max_jetons: int, schema: dict | None = None
    ) -> AsyncIterator[Fragment]:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=consigne,
            max_output_tokens=max_jetons,
            temperature=self.temperature,
        )
        if schema is not None:
            # Sortie structuree en flux : le JSON arrive en fragments partiels,
            # recolles par etiquette.LecteurEtiquette. A verifier au premier appel.
            config.response_mime_type = "application/json"
            config.response_schema = schema
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
            yield usage_vertex(usage)
