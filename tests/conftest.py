from __future__ import annotations

from pathlib import Path

import pytest

from assistant import config
from assistant.corpus.ingestion import construire
from assistant.corpus.mdx import SourceMdx
from assistant.embeddings import FauxEmbedder
from assistant.generation import FauxGenerateur
from assistant.pipeline import Assistant, Parametres
from assistant.recherche import RechercheMemoire

DONNEES = Path(__file__).parent / "donnees"
PORTFOLIO = DONNEES / "portfolio"
SITE = "https://noan-ouedraogo.dev"


@pytest.fixture
def source_exemple() -> SourceMdx:
    return SourceMdx(PORTFOLIO, SITE)


@pytest.fixture
def corpus_exemple(source_exemple):
    return construire(source_exemple)


@pytest.fixture
def reglages_test(tmp_path):
    return config.charger(env={"ASSISTANT_CORPUS_FICHIER": str(tmp_path / "corpus.jsonl")})


@pytest.fixture
def fabriquer_assistant(corpus_exemple):
    async def fabriquer(generateur=None, seuil: float = 0.95, **params) -> Assistant:
        morceaux, coord = corpus_exemple
        embedder = FauxEmbedder(256)
        recherche = await RechercheMemoire.construire(morceaux, embedder)
        p = Parametres(seuil_distance=seuil, site=SITE, **params)
        return Assistant(embedder, recherche, generateur or FauxGenerateur(), p, coord)

    return fabriquer


async def evenements(
    assistant: Assistant, question: str, langue: str = "fr", diagnostic: bool = False
):
    return [e async for e in assistant.repondre(question, langue, diagnostic=diagnostic)]
