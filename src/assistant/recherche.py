"""Recherche : interface, memoire (tests et poste), Firestore (production), variantes.

Toutes renvoient une distance cosinus (1 - similarite) : le seuil de refus a le
meme sens quelle que soit l'implementation. La langue est imposee par le code.

Variantes de la fiche 3.4 et 3.2 de l'etude, sans effet tant qu'elles ne sont pas
choisies dans les reglages :
- `RechercheHybride` (option 4b) : BM25 en memoire et KNN, fusion RRF ;
- `RechercheParente` (option 2d, "petit_grand") : on cherche sur les morceaux,
  on lit la page entiere ;
- `RechercheToutLeCorpus` : tout le site de la langue (architecture C).
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Protocol

from assistant.embeddings import Embedder, jetons
from assistant.modeles import Morceau, Passage, empreinte

# Distance donnee a un passage trouve par BM25 seul : inconnue, donc la pire.
# Le seuil de refus porte toujours sur le meilleur passage vectoriel.
DISTANCE_INCONNUE = 2.0


class Recherche(Protocol):
    async def chercher(
        self,
        vecteur: list[float],
        langue: str,
        k: int,
        projet: str | None = None,
        texte: str | None = None,
    ) -> list[Passage]: ...


def _distance(a: list[float], b: list[float]) -> float:
    return 1.0 - sum(x * y for x, y in zip(a, b, strict=True))


def meilleure_distance(passages: list[Passage]) -> float | None:
    return min((p.distance for p in passages), default=None)


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
            vecteurs = await embedder.vectoriser(
                [m.texte_indexe for m in paquet], "document", [m.titre for m in paquet]
            )
            r._morceaux.extend(paquet)
            r._vecteurs.extend(vecteurs)
        return r

    @property
    def morceaux(self) -> list[Morceau]:
        return list(self._morceaux)

    async def chercher(
        self,
        vecteur: list[float],
        langue: str,
        k: int,
        projet: str | None = None,
        texte: str | None = None,
    ) -> list[Passage]:
        candidats = [
            Passage(m, _distance(vecteur, v))
            for m, v in zip(self._morceaux, self._vecteurs, strict=True)
            if m.langue == langue and (projet is None or m.projet == projet)
        ]
        candidats.sort(key=lambda p: (p.distance, p.morceau.id))
        return candidats[:k]


class RechercheToutLeCorpus:
    """Architecture C : tout le site de la langue, dans l'ordre du corpus (numerotation
    stable pour un corpus donne). Distance nulle par convention : la vraie distance
    du meilleur passage se calcule a part (pipeline, defaut D10)."""

    def __init__(self, morceaux: list[Morceau]) -> None:
        self._morceaux = morceaux

    async def chercher(
        self,
        vecteur: list[float],
        langue: str,
        k: int,
        projet: str | None = None,
        texte: str | None = None,
    ) -> list[Passage]:
        return [Passage(m, 0.0) for m in self._morceaux if m.langue == langue]


class BM25:
    """BM25 sur les jetons du code (`embeddings.jetons` : sans accents, tronques a 6)."""

    def __init__(self, morceaux: list[Morceau], k1: float = 1.2, b: float = 0.75) -> None:
        self.morceaux = morceaux
        self.k1, self.b = k1, b
        self._docs = [Counter(jetons(m.texte_indexe)) for m in morceaux]
        self._longueurs = [sum(d.values()) for d in self._docs]
        self._moyenne = (sum(self._longueurs) / len(self._longueurs)) if morceaux else 0.0
        frequences: Counter[str] = Counter()
        for d in self._docs:
            frequences.update(d.keys())
        n = len(morceaux)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in frequences.items()}

    def scores(self, texte: str) -> list[float]:
        termes = jetons(texte)
        res = []
        for d, longueur in zip(self._docs, self._longueurs, strict=True):
            s = 0.0
            for t in termes:
                f = d.get(t, 0)
                if not f:
                    continue
                norme = self.k1 * (1 - self.b + self.b * longueur / (self._moyenne or 1))
                s += self._idf.get(t, 0.0) * f * (self.k1 + 1) / (f + norme)
            res.append(s)
        return res


def fusion_rrf(classements: list[list[str]], k_rrf: int = 60) -> list[str]:
    """Reciprocal Rank Fusion (Cormack et al. 2009) : somme de 1 / (k + rang)."""
    scores: dict[str, float] = {}
    for classement in classements:
        for rang, ident in enumerate(classement, 1):
            scores[ident] = scores.get(ident, 0.0) + 1.0 / (k_rrf + rang)
    return sorted(scores, key=lambda i: (-scores[i], i))


class RechercheHybride:
    """Option 4b : KNN de la recherche de base et BM25 en memoire, fusionnes par RRF.

    Le texte de la question est necessaire ; sans lui, la recherche de base seule.
    Un passage trouve par BM25 seul recoit une distance inconnue (2.0) : le refus
    par seuil continue de porter sur le meilleur passage vectoriel.
    """

    def __init__(
        self, base: Recherche, morceaux: list[Morceau], k_rrf: int = 60, candidats: int = 20
    ) -> None:
        self.base = base
        self.k_rrf = k_rrf
        self.candidats = candidats
        self._index: dict[tuple[str, str | None], BM25] = {}
        self._morceaux = morceaux

    def _bm25(self, langue: str, projet: str | None) -> BM25:
        cle = (langue, projet)
        if cle not in self._index:
            self._index[cle] = BM25(
                [
                    m
                    for m in self._morceaux
                    if m.langue == langue and (projet is None or m.projet == projet)
                ]
            )
        return self._index[cle]

    async def chercher(
        self,
        vecteur: list[float],
        langue: str,
        k: int,
        projet: str | None = None,
        texte: str | None = None,
    ) -> list[Passage]:
        vectoriels = await self.base.chercher(
            vecteur, langue, max(k, self.candidats), projet, texte
        )
        if not texte:
            return vectoriels[:k]
        index = self._bm25(langue, projet)
        scores = index.scores(texte)
        ordre = sorted(range(len(scores)), key=lambda i: (-scores[i], index.morceaux[i].id))
        lexicaux = [index.morceaux[i] for i in ordre[: self.candidats] if scores[i] > 0]
        par_id = {p.morceau.id: p for p in vectoriels}
        for m in lexicaux:
            par_id.setdefault(m.id, Passage(m, DISTANCE_INCONNUE))
        fusion = fusion_rrf(
            [[p.morceau.id for p in vectoriels], [m.id for m in lexicaux]], self.k_rrf
        )
        return [par_id[i] for i in fusion[:k]]


def page_parente(page: list[Morceau]) -> Morceau:
    """Une page entiere en un seul passage, citee a la page (sans ancre)."""
    premier = page[0]
    texte = "\n\n".join(m.texte_indexe for m in page)
    return Morceau(
        id=f"page-{premier.langue}-{empreinte(premier.url)[:16]}",
        url=premier.url,
        ancre="",
        titre=premier.titre,
        contexte=premier.titre,
        texte=texte,
        langue=premier.langue,
        projet=premier.projet,
        type="page",
        empreinte=empreinte(texte),
    )


class RechercheParente:
    """Option 2d ("petit_grand") : on cherche sur les morceaux, le modele lit la page.

    Les pages se suivent dans l'ordre de leur meilleur morceau, `k` pages au plus,
    chacune avec la distance de son meilleur morceau.
    """

    def __init__(self, base: Recherche, morceaux: list[Morceau], candidats: int = 20) -> None:
        self.base = base
        self.candidats = candidats
        self._pages: dict[tuple[str, str], list[Morceau]] = {}
        for m in morceaux:
            self._pages.setdefault((m.langue, m.url), []).append(m)

    async def chercher(
        self,
        vecteur: list[float],
        langue: str,
        k: int,
        projet: str | None = None,
        texte: str | None = None,
    ) -> list[Passage]:
        trouves = await self.base.chercher(vecteur, langue, max(k, self.candidats), projet, texte)
        sortie: list[Passage] = []
        vues: set[str] = set()
        for p in trouves:
            url = p.morceau.url
            if url in vues:
                continue
            vues.add(url)
            page = self._pages.get((p.morceau.langue, url), [p.morceau])
            sortie.append(Passage(page_parente(page), p.distance))
            if len(sortie) == k:
                break
        return sortie


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
        self,
        vecteur: list[float],
        langue: str,
        k: int,
        projet: str | None = None,
        texte: str | None = None,
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
