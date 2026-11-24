"""Embeddings : interface, faux deterministe pour les tests, Vertex AI pour la production."""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
import unicodedata
from typing import Literal, Protocol

Tache = Literal["question", "document"]

MOTS_VIDES = set(
    [
        "le",
        "la",
        "les",
        "un",
        "une",
        "des",
        "de",
        "du",
        "d",
        "l",
        "et",
        "ou",
        "en",
        "a",
        "au",
        "aux",
        "ce",
        "ces",
        "cet",
        "cette",
        "il",
        "elle",
        "ils",
        "elles",
        "on",
        "qui",
        "que",
        "quoi",
        "dont",
        "ou",
        "est",
        "sont",
        "a",
        "ont",
        "pour",
        "par",
        "sur",
        "dans",
        "avec",
        "sans",
        "pas",
        "ne",
        "plus",
        "se",
        "sa",
        "son",
        "ses",
        "leur",
        "leurs",
        "y",
        "mon",
        "ma",
        "mes",
        "ton",
        "ta",
        "tes",
        "nous",
        "vous",
        "je",
        "tu",
        "me",
        "te",
        "lui",
        "eux",
        "etre",
        "avoir",
        "fait",
        "faire",
        "quel",
        "quelle",
        "quels",
        "quelles",
        "comment",
        "combien",
        "pourquoi",
        "t",
        "il",
        "the",
        "a",
        "an",
        "of",
        "and",
        "or",
        "in",
        "on",
        "at",
        "to",
        "for",
        "by",
        "with",
        "without",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "has",
        "have",
        "had",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "what",
        "which",
        "who",
        "whom",
        "how",
        "why",
        "does",
        "did",
        "do",
        "his",
        "her",
        "their",
        "he",
        "she",
        "they",
        "them",
        "from",
        "as",
        "into",
        "than",
        "then",
        "there",
        "about",
        "can",
        "could",
        "would",
        "should",
    ]
)


def normaliser(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def jetons(texte: str) -> list[str]:
    t = unicodedata.normalize("NFKD", texte.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    mots = re.findall(r"[a-z0-9]+", t)
    return [m[:6] for m in mots if m not in MOTS_VIDES and len(m) > 1]


class Embedder(Protocol):
    nom: str
    dimensions: int

    async def vectoriser(
        self, textes: list[str], tache: Tache, titres: list[str] | None = None
    ) -> list[list[float]]: ...


class FauxEmbedder:
    """Sac de mots hache (mots tronques et trigrammes). Deterministe, hors reseau.

    Il ne mesure rien de la qualite reelle : il sert a tester la chaine.
    """

    nom = "faux-hachage-v1"

    def __init__(self, dimensions: int = 768) -> None:
        self.dimensions = dimensions
        self.appels = 0

    def _indice(self, cle: str) -> tuple[int, float]:
        h = hashlib.blake2b(cle.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(h[:4], "little") % self.dimensions, 1.0 if h[4] & 1 else -1.0

    def _un(self, texte: str) -> list[float]:
        v = [0.0] * self.dimensions
        for mot in jetons(texte):
            i, s = self._indice("m:" + mot)
            v[i] += s
            for k in range(len(mot) - 2):
                i, s = self._indice("t:" + mot[k : k + 3])
                v[i] += 0.3 * s
        return normaliser(v)

    async def vectoriser(
        self, textes: list[str], tache: Tache, titres: list[str] | None = None
    ) -> list[list[float]]:
        self.appels += 1
        return [self._un(t) for t in textes]


class VertexEmbedder:
    """gemini-embedding-001 sur Vertex AI, reduit a 768 dimensions (plafond Firestore : 2 048).

    Non execute par les tests. Un texte par requete : limite du modele sur Vertex
    a verifier au jour 2 ; `parallele` borne les requetes simultanees.
    `tache_question` : RETRIEVAL_QUERY (servi) ou QUESTION_ANSWERING (option 3b).
    `titre_document` : le titre de la page accompagne chaque document (option 3c,
    permis seulement avec RETRIEVAL_DOCUMENT).
    """

    def __init__(
        self,
        projet: str,
        region: str,
        modele: str,
        dimensions: int = 768,
        parallele: int = 4,
        tache_question: str = "RETRIEVAL_QUERY",
        titre_document: bool = False,
    ) -> None:
        from google import genai  # import paresseux : dependance optionnelle

        self._client = genai.Client(vertexai=True, project=projet, location=region)
        self.nom = modele
        self.dimensions = dimensions
        self.tache_question = tache_question
        self.titre_document = titre_document
        self._sem = asyncio.Semaphore(parallele)

    async def _un(self, texte: str, tache: Tache, titre: str | None) -> list[float]:
        from google.genai import types

        config = types.EmbedContentConfig(
            task_type=self.tache_question if tache == "question" else "RETRIEVAL_DOCUMENT",
            output_dimensionality=self.dimensions,
        )
        if tache == "document" and self.titre_document and titre:
            config.title = titre
        async with self._sem:
            r = await self._client.aio.models.embed_content(
                model=self.nom, contents=texte, config=config
            )
        # Sous 3 072 dimensions, le vecteur n'est pas normalise par le modele.
        return normaliser(list(r.embeddings[0].values))

    async def vectoriser(
        self, textes: list[str], tache: Tache, titres: list[str] | None = None
    ) -> list[list[float]]:
        noms = titres or [None] * len(textes)
        return list(
            await asyncio.gather(
                *(self._un(t, tache, n) for t, n in zip(textes, noms, strict=True))
            )
        )
