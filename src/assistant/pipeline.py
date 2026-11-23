"""Le trajet d'une question, chemin fixe par le code.

1. Aiguiller (code) : donnees personnelles -> refus ecrit ; contact -> carte.
2. Chercher : vecteur de la question, KNN filtre sur la langue de la page.
3. Decider (code) : meilleur passage au-dessus du seuil de distance -> refus
   ecrit d'avance, le modele n'est pas appele.
4. Rediger en flux, a partir des seuls passages numerotes, 450 jetons au plus.
   Si le modele repond [REFUS], le code affiche le refus ecrit d'avance.
5. Filtrer en flux (chiffres, liens, courriels, tirets), puis les sources citees.

Pas de boucle d'agent : un seul appel au modele par question (revision du 19/11).
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

from assistant import textes
from assistant.embeddings import Embedder
from assistant.filtres import FiltreFlux, chiffres_autorises, citations_valides
from assistant.garde import aiguiller
from assistant.generation import MARQUEUR_REFUS, Generateur
from assistant.modeles import Bilan, Evenement, Passage
from assistant.recherche import Recherche

CONSIGNE_DEFAUT = Path(__file__).resolve().parents[2] / "prompts" / "redaction.md"


@dataclass
class Parametres:
    seuil_distance: float
    passages: int = 6
    jetons_reponse: int = 450
    delai_s: float = 30
    domaines: tuple[str, ...] = ("noan-ouedraogo.dev", "linkedin.com")
    site: str = "https://noan-ouedraogo.dev"
    tarif_entree: float = 0.25
    tarif_sortie: float = 1.50
    appliquer_seuil: bool = True
    versions: dict = field(default_factory=dict)


def construire_contenu(question: str, passages: list[Passage]) -> str:
    blocs = [f"Question : {question}", "", "Passages :"]
    for i, p in enumerate(passages, 1):
        blocs.append(f"[{i}] {p.morceau.contexte}\n{p.morceau.texte}")
    return "\n".join(blocs)


def empreinte_consigne(consigne: str) -> str:
    return hashlib.sha256(consigne.encode("utf-8")).hexdigest()[:12]


class Assistant:
    branche = "chemin"

    def __init__(
        self,
        embedder: Embedder,
        recherche: Recherche,
        generateur: Generateur,
        parametres: Parametres,
        coordonnees: dict,
        consigne: str | None = None,
    ) -> None:
        self.embedder = embedder
        self.recherche = recherche
        self.generateur = generateur
        self.p = parametres
        self.coordonnees = coordonnees
        self.consigne = consigne or CONSIGNE_DEFAUT.read_text(encoding="utf-8")
        self.version_consigne = empreinte_consigne(self.consigne)

    def _carte(self, langue: str) -> dict:
        c = self.coordonnees
        return {
            "courriel": c.get("courriel", ""),
            "linkedin": c.get("linkedin", ""),
            "cv": c.get("cv", {}).get(langue) or c.get("cv", {}).get("fr", ""),
            "contact": c.get("contact", {}).get(langue, ""),
        }

    def _sources(self, passages: list[Passage], cites: list[int]) -> list[dict]:
        choisis = cites or []
        if not choisis:  # rien de cite : on montre les pages consultees, marquees comme telles
            vus, choisis = set(), []
            for i, p in enumerate(passages, 1):
                if p.morceau.lien not in vus:
                    vus.add(p.morceau.lien)
                    choisis.append(i)
                if len(choisis) == 3:
                    break
        res = []
        for n in choisis[:5]:
            m = passages[n - 1].morceau
            res.append(
                {
                    "n": n,
                    "titre": m.titre,
                    "section": m.contexte,
                    "url": self.p.site.rstrip("/") + m.lien,
                }
            )
        return res

    def _aiguillage(self, question: str, langue: str) -> tuple[list[Evenement], str] | None:
        """Avant toute recherche et tout modele : donnees personnelles -> refus ecrit,
        contact -> carte rendue par le code. None : la question suit son cours."""
        route = aiguiller(question)
        if route == "personnel":
            texte = textes.texte(langue, "personnel")
            return [Evenement("refus", {"motif": "personnel", "texte": texte})], "refus_personnel"
        if route == "contact":
            return self._evenements_carte(langue), "carte"
        return None

    def _evenements_carte(self, langue: str) -> list[Evenement]:
        return [
            Evenement("carte", self._carte(langue)),
            Evenement("texte", {"texte": textes.texte(langue, "contact")}),
        ]

    def _fin(
        self,
        chemin: str,
        bilan: Bilan,
        t0: float,
        diagnostic: bool,
        passages: list[Passage],
        brut: str,
        affiche: str,
    ) -> list[Evenement]:
        """Bilan, cout et evenements de fin, communs aux branches A et B."""
        bilan.chemin = chemin
        bilan.duree_ms = round((time.perf_counter() - t0) * 1000, 1)
        bilan.cout_usd = round(
            bilan.jetons_entree * self.p.tarif_entree / 1e6
            + bilan.jetons_sortie * self.p.tarif_sortie / 1e6,
            6,
        )
        evts = []
        if diagnostic:
            evts.append(
                Evenement(
                    "diagnostic",
                    {
                        # Le texte exact transmis au modele (contenu public) :
                        # le juge en a besoin pour accepter un fait ajoute exact.
                        "passages": [
                            {
                                "n": i,
                                "id": p.morceau.id,
                                "url": p.morceau.url,
                                "projet": p.morceau.projet,
                                "distance": round(p.distance, 4),
                                "texte": p.morceau.texte_indexe,
                            }
                            for i, p in enumerate(passages, 1)
                        ],
                        "brut": brut,
                        "affiche": affiche,
                    },
                )
            )
        evts.append(Evenement("fin", {"chemin": chemin, "bilan": asdict(bilan)}))
        return evts

    async def repondre(
        self, question: str, langue: str, diagnostic: bool = False
    ) -> AsyncIterator[Evenement]:
        t0 = time.perf_counter()
        bilan = Bilan()
        passages: list[Passage] = []
        brut = ""
        affiche = ""

        def fin(chemin: str) -> list[Evenement]:
            return self._fin(chemin, bilan, t0, diagnostic, passages, brut, affiche)

        ecrit = self._aiguillage(question, langue)
        if ecrit:
            evts, chemin = ecrit
            for e in evts + fin(chemin):
                yield e
            return

        yield Evenement("etape", {"texte": textes.texte(langue, "etape_recherche")})
        vecteur = (await self.embedder.vectoriser([question], "question"))[0]
        passages = await self.recherche.chercher(vecteur, langue, self.p.passages)
        bilan.passages = len(passages)
        bilan.meilleure_distance = round(passages[0].distance, 4) if passages else None
        if not passages or (
            self.p.appliquer_seuil and passages[0].distance > self.p.seuil_distance
        ):
            yield Evenement(
                "refus", {"motif": "hors_corpus", "texte": textes.texte(langue, "hors_corpus")}
            )
            for e in fin("refus_seuil"):
                yield e
            return

        yield Evenement("etape", {"texte": textes.texte(langue, "etape_redaction")})
        filtre = FiltreFlux(
            chiffres_autorises(p.morceau.texte_indexe for p in passages), self.p.domaines, langue
        )
        contenu = construire_contenu(question, passages)
        demarre = False
        refus_modele = False
        bilan.appels_modele = 1
        try:
            async with asyncio.timeout(self.p.delai_s):
                async for frag in self.generateur.rediger(
                    self.consigne, contenu, self.p.jetons_reponse
                ):
                    if frag.jetons_entree is not None:
                        bilan.jetons_entree += frag.jetons_entree
                        bilan.jetons_sortie += frag.jetons_sortie or 0
                    if not frag.texte:
                        continue
                    brut += frag.texte
                    if not demarre:
                        tete = brut.lstrip()
                        if MARQUEUR_REFUS.startswith(tete):
                            continue  # peut encore devenir [REFUS] : on retient
                        if tete.startswith(MARQUEUR_REFUS):
                            refus_modele = True
                            continue  # on lit la fin du flux pour compter les jetons
                        demarre = True
                        morceau = tete
                    else:
                        morceau = frag.texte
                    if refus_modele:
                        continue
                    sortie = filtre.pousser(morceau)
                    if sortie:
                        if bilan.premier_mot_ms is None:
                            bilan.premier_mot_ms = round((time.perf_counter() - t0) * 1000, 1)
                        affiche += sortie
                        yield Evenement("texte", {"texte": sortie})
        except TimeoutError:
            yield Evenement("erreur", {"texte": textes.texte(langue, "erreur")})
            for e in fin("delai"):
                yield e
            return

        if refus_modele or not brut.strip() or brut.strip() == MARQUEUR_REFUS:
            yield Evenement(
                "refus", {"motif": "modele", "texte": textes.texte(langue, "refus_modele")}
            )
            for e in fin("refus_modele"):
                yield e
            return

        reste = (filtre.pousser(brut.lstrip()) if not demarre else "") + filtre.terminer()
        if reste:
            affiche += reste
            yield Evenement("texte", {"texte": reste})
        cites = citations_valides(affiche, len(passages))
        bilan.sources_citees = len(cites)
        bilan.chiffres_remplaces = filtre.compteurs.chiffres_remplaces
        bilan.liens_retires = filtre.compteurs.liens_retires
        yield Evenement(
            "sources", {"citees": bool(cites), "sources": self._sources(passages, cites)}
        )
        for e in fin("reponse"):
            yield e
