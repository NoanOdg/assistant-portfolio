from __future__ import annotations

from pathlib import Path

import altcha
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


# Cout ALTCHA reduit pour la vitesse des tests ; test_altcha.py joue aussi les
# reglages reels.
# Les tests jouent la memoire active (coupee en production tant que la mesure
# n a pas tranche) ; test_memoire_coupee la coupe explicitement.
ENV_TEST = {"ASSISTANT_ALTCHA_COUT": "10", "ASSISTANT_MEMOIRE_ACTIVE": "true"}


@pytest.fixture
def reglages_test(tmp_path):
    return config.charger(
        env={**ENV_TEST, "ASSISTANT_CORPUS_FICHIER": str(tmp_path / "corpus.jsonl")}
    )


def resoudre(defi: dict) -> str:
    """Ce que fait le widget : resoudre le defi, encoder la charge en base64."""
    challenge = altcha.Challenge.from_dict(defi)
    solution = altcha.solve_challenge(challenge, timeout=30)
    assert solution is not None
    return altcha.Payload(challenge, solution).to_base64()


def ouvrir_session(client) -> str:
    """GET /defi, resolution, POST /session : rend le jeton de session."""
    charge = resoudre(client.get("/defi").json())
    r = client.post("/session", json={"altcha": charge})
    assert r.status_code == 200, r.text
    return r.json()["session"]


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
