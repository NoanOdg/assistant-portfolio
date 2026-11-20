"""Recherche : interface, memoire (tests et poste), Firestore (production), tout le corpus.

Toutes renvoient une distance cosinus (1 - similarite) : le seuil de refus a le
meme sens quelle que soit l'implementation. La langue est imposee par le code.
"""

from __future__ import annotations

from typing import Protocol

from assistant.embeddings import Embedder
from assistant.modeles import Morceau, Passage


class Recherche(Protocol):
    async def chercher(
        self, vecteur: list[float], langue: str, k: int, projet: str | None = None
    ) -> list[Passage]: ...


def _distance(a: list[float], b: list[float]) -> float:
    return 1.0 - sum(x * y for x, y in zip(a, b, strict=True))


class RechercheMemoire:
    """KNN exact en memoire. 600 morceaux tiennent sans difficulte."""

    def __init__(self) -> None:
        self._morceaux: list[Morceau] = []
        self._vecteurs: list[list[float]] = []

    @classmethod
    async def construire(
        cls, morceaux: list[Morceau], embedder: Embedder, lot: int = 64
    ) -> RechercheMemoire:
        r = cls()
        for i in range(0, len(morceaux), lot):
            paquet = morceaux[i : i + lot]
            vecteurs = await embedder.vectoriser([m.texte_indexe for m in paquet], "document")
            r._morceaux.extend(paquet)
            r._vecteurs.extend(vecteurs)
        return r

    @property
    def morceaux(self) -> list[Morceau]:
        return list(self._morceaux)

    async def chercher(
        self, vecteur: list[float], langue: str, k: int, projet: str | None = None
    ) -> list[Passage]:
        candidats = [
            Passage(m, _distance(vecteur, v))
            for m, v in zip(self._morceaux, self._vecteurs, strict=True)
            if m.langue == langue and (projet is None or m.projet == projet)
        ]
        candidats.sort(key=lambda p: (p.distance, p.morceau.id))
        return candidats[:k]


class RechercheToutLeCorpus:
    """Branche de comparaison C : tout le site de la langue dans la consigne, sans recherche.

    Distance nulle par convention : le refus par seuil ne s'applique pas,
    c'est au modele de refuser. Mesure de reference, pas de production.
    """

    def __init__(self, morceaux: list[Morceau]) -> None:
        self._morceaux = morceaux

    async def chercher(
        self, vecteur: list[float], langue: str, k: int, projet: str | None = None
    ) -> list[Passage]:
        return [Passage(m, 0.0) for m in self._morceaux if m.langue == langue]


class RechercheFirestore:
    """Recherche vectorielle Firestore (KNN, index flat, distance cosinus).

    Structure : corpus/{version}/morceaux/{id}. Chaque revision Cloud Run sert
    une version fixee au deploiement : revenir en arriere = rerouter le trafic.
    Index composites (langue, embedding) et (langue, projet, embedding) poses
    par Terraform sur le groupe de collections `morceaux`. Non execute par les tests.
    """

    def __init__(self, projet: str, collection: str, version: str, base: str = "(default)") -> None:
        from google.cloud import firestore  # import paresseux : dependance optionnelle

        self._db = firestore.AsyncClient(project=projet, database=base)
        self._col = self._db.collection(collection).document(version).collection("morceaux")

    async def chercher(
        self, vecteur: list[float], langue: str, k: int, projet: str | None = None
    ) -> list[Passage]:
        from google.cloud.firestore_v1.base_query import FieldFilter
        from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
        from google.cloud.firestore_v1.vector import Vector

        requete = self._col.where(filter=FieldFilter("langue", "==", langue))
        if projet:
            requete = requete.where(filter=FieldFilter("projet", "==", projet))
        knn = requete.find_nearest(
            vector_field="embedding",
            query_vector=Vector(vecteur),
            distance_measure=DistanceMeasure.COSINE,
            limit=k,
            distance_result_field="distance",
        )
        passages = []
        for doc in await knn.get():
            d = doc.to_dict()
            distance = float(d.pop("distance"))
            d.pop("embedding", None)
            passages.append(Passage(Morceau.depuis_dict(d), distance))
        return passages
