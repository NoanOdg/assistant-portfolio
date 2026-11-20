"""Indexation : vectorise le corpus et l'ecrit dans une version neuve.

Seuls les morceaux nouveaux ou modifies (empreinte SHA-256) sont vectorises ;
les autres reprennent le vecteur de la version precedente. La version neuve ne
sert rien tant qu'une revision Cloud Run ne la designe pas (ASSISTANT_CORPUS_VERSION).

    assistant-indexer --corpus build/corpus.jsonl --version 20261119-abc1234 \
        --precedente 20261112-def5678
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from assistant.corpus.ingestion import GardeSuppression, lire
from assistant.embeddings import Embedder
from assistant.modeles import Morceau

PART_SUPPRESSION_MAX = 0.30


class Magasin(Protocol):
    async def lire(self, version: str) -> dict[str, tuple[str, list[float]]]: ...
    async def ecrire(self, version: str, elements: list[tuple[Morceau, list[float]]]) -> None: ...


class MagasinMemoire:
    def __init__(self) -> None:
        self.versions: dict[str, dict[str, tuple[Morceau, list[float]]]] = {}

    async def lire(self, version: str) -> dict[str, tuple[str, list[float]]]:
        return {i: (m.empreinte, v) for i, (m, v) in self.versions.get(version, {}).items()}

    async def ecrire(self, version: str, elements: list[tuple[Morceau, list[float]]]) -> None:
        self.versions.setdefault(version, {}).update({m.id: (m, v) for m, v in elements})


class MagasinFirestore:
    """corpus/{version}/morceaux/{id}, vecteur dans le champ `embedding`. Non teste hors ligne."""

    def __init__(self, projet: str, collection: str = "corpus") -> None:
        from google.cloud import firestore

        self._db = firestore.AsyncClient(project=projet)
        self._collection = collection

    def _col(self, version: str):
        return self._db.collection(self._collection).document(version).collection("morceaux")

    async def lire(self, version: str) -> dict[str, tuple[str, list[float]]]:
        res = {}
        async for doc in self._col(version).stream():
            d = doc.to_dict()
            res[doc.id] = (d["empreinte"], list(d["embedding"]))
        return res

    async def ecrire(self, version: str, elements: list[tuple[Morceau, list[float]]]) -> None:
        from google.cloud.firestore_v1.vector import Vector

        for i in range(0, len(elements), 400):  # lots sous la limite de 500 ecritures
            lot = self._db.batch()
            for m, v in elements[i : i + 400]:
                lot.set(
                    self._col(version).document(m.id), {**m.vers_dict(), "embedding": Vector(v)}
                )
            await lot.commit()


@dataclass
class BilanIndexation:
    total: int
    vectorises: int
    repris: int
    part_supprimee: float


async def indexer(
    morceaux: list[Morceau],
    embedder: Embedder,
    magasin: Magasin,
    version: str,
    precedente: str | None = None,
    lot: int = 32,
) -> BilanIndexation:
    anciens = await magasin.lire(precedente) if precedente else {}
    if anciens:
        ids = {m.id for m in morceaux}
        part = sum(1 for i in anciens if i not in ids) / len(anciens)
        if part > PART_SUPPRESSION_MAX:
            raise GardeSuppression(f"{part:.0%} des morceaux disparaissent : rien n'est ecrit")
    else:
        part = 0.0
    repris, a_vectoriser = [], []
    for m in morceaux:
        ancien = anciens.get(m.id)
        if ancien and ancien[0] == m.empreinte:
            repris.append((m, ancien[1]))
        else:
            a_vectoriser.append(m)
    nouveaux = []
    for i in range(0, len(a_vectoriser), lot):
        paquet = a_vectoriser[i : i + lot]
        vecteurs = await embedder.vectoriser([m.texte_indexe for m in paquet], "document")
        nouveaux.extend(zip(paquet, vecteurs, strict=True))
    await magasin.ecrire(version, repris + nouveaux)
    return BilanIndexation(len(morceaux), len(nouveaux), len(repris), part)


def main(argv: list[str] | None = None) -> int:
    from assistant import config
    from assistant.embeddings import VertexEmbedder

    p = argparse.ArgumentParser(description="Vectorise le corpus dans une version Firestore.")
    p.add_argument("--corpus", type=Path, default=Path("build/corpus.jsonl"))
    p.add_argument("--version", required=True)
    p.add_argument("--precedente")
    a = p.parse_args(argv)
    r = config.charger()
    m = r.moteur
    embedder = VertexEmbedder(
        m["projet_gcp"], m["region"], m["modele_embeddings"], int(m["dimensions"])
    )
    magasin = MagasinFirestore(m["projet_gcp"], r.corpus["collection"])
    try:
        bilan = asyncio.run(indexer(lire(a.corpus), embedder, magasin, a.version, a.precedente))
    except GardeSuppression as e:
        print(f"ECHEC : {e}", file=sys.stderr)
        return 2
    print(f"version {a.version} : {bilan}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
