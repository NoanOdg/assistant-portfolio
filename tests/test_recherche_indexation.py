from __future__ import annotations

import math

import pytest

from assistant.corpus.ingestion import GardeSuppression
from assistant.embeddings import FauxEmbedder
from assistant.indexation import MagasinMemoire, indexer
from assistant.modeles import Morceau
from assistant.recherche import RechercheMemoire, RechercheToutLeCorpus


async def test_faux_embedder_deterministe_et_normalise():
    e = FauxEmbedder(128)
    a, b, c = await e.vectoriser(
        ["moniteur de pannes", "moniteur de pannes", "recette"], "document"
    )
    assert a == b
    assert math.isclose(sum(x * x for x in a), 1.0, rel_tol=1e-9)
    assert a != c


async def test_recherche_filtre_langue_et_projet(corpus_exemple):
    morceaux, _ = corpus_exemple
    e = FauxEmbedder(256)
    r = await RechercheMemoire.construire(morceaux, e)
    v = (await e.vectoriser(["taux de détection des pannes"], "question"))[0]
    passages = await r.chercher(v, "fr", 4)
    assert len(passages) == 4
    assert all(p.morceau.langue == "fr" for p in passages)
    assert [p.distance for p in passages] == sorted(p.distance for p in passages)
    assert passages[0].morceau.projet == "exemple"
    seuls = await r.chercher(v, "fr", 10, projet="parcours")
    assert {p.morceau.projet for p in seuls} == {"parcours"}


async def test_tout_le_corpus_rend_toute_la_langue(corpus_exemple):
    morceaux, _ = corpus_exemple
    r = RechercheToutLeCorpus(morceaux)
    p = await r.chercher([], "en", 3)
    assert len(p) == sum(1 for m in morceaux if m.langue == "en")


def _variante(m: Morceau, texte: str) -> Morceau:
    return Morceau(**{**m.vers_dict(), "texte": texte, "empreinte": "autre"})


async def test_indexation_incrementale(corpus_exemple):
    morceaux, _ = corpus_exemple
    e = FauxEmbedder(64)
    magasin = MagasinMemoire()
    b1 = await indexer(morceaux, e, magasin, "v1")
    assert (b1.vectorises, b1.repris) == (len(morceaux), 0)
    modifies = [_variante(morceaux[0], "texte change"), *morceaux[1:]]
    b2 = await indexer(modifies, e, magasin, "v2", precedente="v1")
    assert (b2.vectorises, b2.repris) == (1, len(morceaux) - 1)
    assert set(magasin.versions["v1"]) == set(magasin.versions["v2"])


async def test_indexation_refuse_une_hemorragie(corpus_exemple):
    morceaux, _ = corpus_exemple
    e = FauxEmbedder(64)
    magasin = MagasinMemoire()
    await indexer(morceaux, e, magasin, "v1")
    with pytest.raises(GardeSuppression):
        await indexer(morceaux[:2], e, magasin, "v2", precedente="v1")
    assert "v2" not in magasin.versions
