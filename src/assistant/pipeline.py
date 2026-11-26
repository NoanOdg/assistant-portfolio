"""Le trajet d'une question, chemin fixe par le code.

1. Aiguiller (code) : donnees personnelles -> refus ecrit ; contact -> carte.
2. Chercher : vecteur de la question, KNN filtre sur la langue de la page.
   En tout_le_corpus (architecture C), tout le site de la langue est transmis ;
   la distance du meilleur passage est calculee quand meme (journal, prefiltre).
3. Decider (code) : meilleur passage au-dessus du seuil de distance -> refus
   ecrit d'avance, le modele n'est pas appele (en C, seulement si le prefiltre
   est choisi).
4. Rediger en flux, a partir des seuls passages numerotes, 450 jetons au plus.
   Si le modele repond [REFUS] (ou rien d'autre d'utile), le code affiche le
   refus ecrit d'avance ; un [REFUS] au milieu d'un texte n'est pas affiche. En
   sortie structuree, l'intention donnee par le modele (contact, personnel,
   hors_sujet) fait afficher par le code la carte ou le refus ecrit d'avance.
5. Filtrer en flux (chiffres, liens, courriels, tirets), puis les sources citees.

Pas de boucle d'agent : un seul appel au modele par question (revision du 19/11).
Chaque option de l'etude du 24/11/2026 est un reglage ; les valeurs par defaut
reproduisent le service tel qu'il etait servi.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import hashlib
import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from assistant import textes
from assistant.embeddings import Embedder
from assistant.etiquette import SCHEMA, LecteurEtiquette
from assistant.filtres import (
    FiltreFlux,
    chiffres_autorises,
    chiffres_non_attribues,
    citations_valides,
)
from assistant.garde import aiguiller
from assistant.generation import MARQUEUR, Generateur, utile
from assistant.modeles import Bilan, Evenement, Morceau, Passage
from assistant.recherche import Recherche, meilleure_distance

PROMPTS = Path(__file__).resolve().parents[2] / "prompts"
CONSIGNE_DEFAUT = PROMPTS / "redaction.md"
CONSIGNES = {"v1": PROMPTS / "redaction.md", "v2": PROMPTS / "redaction-v2.md"}
BLOC_ETIQUETTE = PROMPTS / "etiquette.md"
COMMENTAIRE = re.compile(r"<!--.*?-->\s*", re.S)


def charger_consigne(version: str = "v1", etiquette: bool = False) -> str:
    """Texte de la consigne envoye au modele. Les commentaires HTML du fichier
    (classe d'erreurs visee par chaque regle) ne partent pas au modele."""
    texte = COMMENTAIRE.sub("", CONSIGNES[version].read_text(encoding="utf-8"))
    if etiquette:
        texte = (
            texte.rstrip("\n")
            + "\n\n"
            + COMMENTAIRE.sub("", BLOC_ETIQUETTE.read_text(encoding="utf-8"))
        )
    return texte


@dataclass
class Parametres:
    seuil_distance: float
    passages: int = 6
    jetons_reponse: int = 450
    delai_s: float = 30
    # Premier fragment du modele attendu au plus delai_premier_s ; sinon l'appel
    # est annule et rejoue (relances_premier fois). Rien n'est encore affiche,
    # donc la relance est invisible. Vu le 24/11/2026 : 3 questions sur 40 sans
    # un mot en 30 s sur Vertex, contre environ 1 s d'habitude.
    delai_premier_s: float = 8
    relances_premier: int = 1
    domaines: tuple[str, ...] = ("noan-ouedraogo.dev", "linkedin.com")
    site: str = "https://noan-ouedraogo.dev"
    # Dollars par million de jetons du modele de redaction.
    tarif_entree: float = 0.25
    tarif_sortie: float = 1.50
    tarif_cache: float = 0.025
    appliquer_seuil: bool = True
    # Options de l'etude (defauts = service servi).
    mode_recherche: str = "rag"  # "rag" ou "tout_le_corpus"
    prefiltre_seuil: bool = False  # tout_le_corpus seulement (option 7c)
    ordre_contenu: str = "question_d_abord"  # ou "passages_d_abord" (D1)
    date_du_jour: bool = False  # D7
    aiguillage: str = "v1"  # ou "classes" (option 6b)
    etiquette_modele: bool = False  # option 6c, sortie structuree
    aujourd_hui: Callable[[], dt.date] = dt.date.today
    versions: dict = field(default_factory=dict)


def _donnees(texte: str) -> str:
    """Une balise fermante dans une donnee ne doit pas fermer le bloc qui la contient."""
    return texte.replace("</", "< /")


def construire_contenu(
    question: str,
    passages: list[Passage],
    ordre: str = "question_d_abord",
    date: dt.date | None = None,
) -> str:
    """Le contenu envoye au modele (la consigne part a part, en instruction systeme).

    "question_d_abord" : format servi. "passages_d_abord" : [passages][date][question],
    chaque bloc entre balises declarees donnees par la consigne ; pour une langue
    donnee, le prefixe [consigne][passages] est identique d'une question a
    l'autre en tout_le_corpus, ce qui permet au cache implicite de jouer.
    """
    if ordre == "passages_d_abord":
        blocs = ["<passages>"]
        for i, p in enumerate(passages, 1):
            blocs.append(f"[{i}] {_donnees(p.morceau.contexte)}\n{_donnees(p.morceau.texte)}")
        blocs.append("</passages>")
        if date is not None:
            blocs += ["", f"<date_du_jour>{date.isoformat()}</date_du_jour>"]
        blocs += ["", "<question>", _donnees(question), "</question>"]
        return "\n".join(blocs)
    blocs = [f"Question : {question}"]
    if date is not None:
        blocs.append(f"Date du jour : {date.isoformat()}")
    blocs += ["", "Passages :"]
    for i, p in enumerate(passages, 1):
        blocs.append(f"[{i}] {p.morceau.contexte}\n{p.morceau.texte}")
    return "\n".join(blocs)


def empreinte_consigne(consigne: str) -> str:
    return hashlib.sha256(consigne.encode("utf-8")).hexdigest()[:12]


def compter(bilan: Bilan, frag) -> None:
    """Ajoute les compteurs de jetons d'un fragment de fin d'appel au bilan."""
    if getattr(frag, "jetons_entree", None) is None:
        return
    bilan.jetons_entree += frag.jetons_entree
    bilan.jetons_sortie += getattr(frag, "jetons_sortie", 0) or 0
    bilan.jetons_cache += getattr(frag, "jetons_cache", 0) or 0
    bilan.jetons_reflexion += getattr(frag, "jetons_reflexion", 0) or 0


def couts(bilan: Bilan, entree: float, sortie: float, cache: float) -> tuple[float, float]:
    """(cout reel, cout de production sans cache), en dollars.

    L'entree lue dans le cache est facturee au prix du cache ; la reflexion au
    prix de la sortie. Le cout de production compte toute l'entree au plein
    tarif : c'est celui que plafonne la regle R0 (faible trafic, cache froid)."""
    en_cache = min(bilan.jetons_cache, bilan.jetons_entree)
    generes = bilan.jetons_sortie + bilan.jetons_reflexion
    reel = ((bilan.jetons_entree - en_cache) * entree + en_cache * cache + generes * sortie) / 1e6
    production = (bilan.jetons_entree * entree + generes * sortie) / 1e6
    return round(reel, 6), round(production, 6)


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
        corpus: list[Morceau] | None = None,
    ) -> None:
        self.embedder = embedder
        self.recherche = recherche
        self.generateur = generateur
        self.p = parametres
        self.coordonnees = coordonnees
        self.consigne = consigne or CONSIGNE_DEFAUT.read_text(encoding="utf-8")
        self.version_consigne = empreinte_consigne(self.consigne)
        # Architecture C : les morceaux servis, dans l'ordre du corpus.
        self.corpus = corpus or []
        if self.p.mode_recherche == "tout_le_corpus" and not self.corpus:
            raise ValueError("tout_le_corpus exige le corpus servi")

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
        route = aiguiller(question, self.p.aiguillage)
        if route == "consigne":
            texte = textes.texte(langue, "consigne")
            return [Evenement("refus", {"motif": "consigne", "texte": texte})], "refus_consigne"
        if route == "personnel":
            texte = textes.texte(langue, "personnel")
            return [Evenement("refus", {"motif": "personnel", "texte": texte})], "refus_personnel"
        if route == "contact":
            return self._evenements_carte(langue), "carte"
        if route == "telephone":
            return self._evenements_carte(langue, "contact_telephone"), "carte"
        return None

    def _evenements_carte(self, langue: str, cle: str = "contact") -> list[Evenement]:
        return [
            Evenement("carte", self._carte(langue)),
            Evenement("texte", {"texte": textes.texte(langue, cle)}),
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
        bilan.cout_usd, bilan.cout_production_usd = couts(
            bilan, self.p.tarif_entree, self.p.tarif_sortie, self.p.tarif_cache
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
                        "cites": citations_valides(affiche, len(passages)),
                        "architecture": "C" if self.p.mode_recherche == "tout_le_corpus" else "",
                        "brut": brut,
                        "affiche": affiche,
                    },
                )
            )
        evts.append(Evenement("fin", {"chemin": chemin, "bilan": asdict(bilan)}))
        return evts

    async def _passages(
        self, question: str, langue: str, bilan: Bilan
    ) -> tuple[list[Passage], bool]:
        """(passages a transmettre, refus par le seuil)."""
        vecteur = (await self.embedder.vectoriser([question], "question"))[0]
        if self.p.mode_recherche == "tout_le_corpus":
            proches = await self.recherche.chercher(vecteur, langue, 1, texte=question)
            d = meilleure_distance(proches)
            bilan.meilleure_distance = round(d, 4) if d is not None else None
            if self.p.prefiltre_seuil and (d is None or d > self.p.seuil_distance):
                return [], True
            passages = [Passage(m, 0.0) for m in self.corpus if m.langue == langue]
            return passages, not passages
        passages = await self.recherche.chercher(vecteur, langue, self.p.passages, texte=question)
        d = meilleure_distance(passages)
        bilan.meilleure_distance = round(d, 4) if d is not None else None
        refus = not passages or (self.p.appliquer_seuil and d > self.p.seuil_distance)
        return passages, refus

    async def _rediger(self, bilan, *args, **kwargs):
        """Flux du modele, rejoue si le premier fragment tarde (voir delai_premier_s)."""
        for essai in range(self.p.relances_premier + 1):
            flux = self.generateur.rediger(*args, **kwargs).__aiter__()
            try:
                premier = await asyncio.wait_for(anext(flux), self.p.delai_premier_s)
            except TimeoutError:
                with contextlib.suppress(Exception):
                    await flux.aclose()
                if essai == self.p.relances_premier:
                    raise
                bilan.appels_modele += 1
                continue
            except StopAsyncIteration:
                return
            yield premier
            async for frag in flux:
                yield frag
            return

    async def repondre(
        self, question: str, langue: str, diagnostic: bool = False
    ) -> AsyncIterator[Evenement]:
        t0 = time.perf_counter()
        bilan = Bilan()
        passages: list[Passage] = []
        brut = ""  # ce que le modele a ecrit, JSON compris
        recu = ""  # texte de reponse recu (decode si sortie structuree)
        affiche = ""

        def fin(chemin: str) -> list[Evenement]:
            return self._fin(chemin, bilan, t0, diagnostic, passages, brut, affiche)

        ecrit = self._aiguillage(question, langue)
        if ecrit:
            evts, chemin = ecrit
            bilan.aiguillage = "code"
            for e in evts + fin(chemin):
                yield e
            return

        yield Evenement("etape", {"texte": textes.texte(langue, "etape_recherche")})
        passages, refus_seuil = await self._passages(question, langue, bilan)
        bilan.passages = len(passages)
        if refus_seuil:
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
        contenu = construire_contenu(
            question,
            passages,
            self.p.ordre_contenu,
            self.p.aujourd_hui() if self.p.date_du_jour else None,
        )
        lecteur = LecteurEtiquette() if self.p.etiquette_modele else None
        demarre = False
        refus_modele = False
        bilan.appels_modele = 1

        def traiter(texte: str) -> list[Evenement]:
            """Texte de reponse : retenu tant qu'il ne dit rien d'utile (il peut
            encore n'etre qu'un [REFUS]), puis filtre."""
            nonlocal recu, demarre, refus_modele, affiche
            recu += texte
            if not demarre:
                tete = recu.lstrip()
                if not utile(tete):
                    return []  # marqueur, renvoi ou debut de crochet : on retient
                if MARQUEUR.match(tete):
                    refus_modele = True
                    return []  # on lit la fin du flux pour compter les jetons
                demarre = True
                morceau = tete
            else:
                morceau = texte
            if refus_modele:
                return []
            sortie = filtre.pousser(morceau)
            if not sortie:
                return []
            if bilan.premier_mot_ms is None:
                bilan.premier_mot_ms = round((time.perf_counter() - t0) * 1000, 1)
            affiche += sortie
            return [Evenement("texte", {"texte": sortie})]

        try:
            async with asyncio.timeout(self.p.delai_s):
                async for frag in self._rediger(
                    bilan,
                    self.consigne,
                    contenu,
                    self.p.jetons_reponse,
                    **({"schema": SCHEMA} if lecteur else {}),
                ):
                    compter(bilan, frag)
                    if not frag.texte:
                        continue
                    brut += frag.texte
                    texte = frag.texte
                    if lecteur is not None:
                        texte = lecteur.pousser(frag.texte)
                        if lecteur.intention != "question":
                            continue  # inconnue pour l'instant, ou rendue par le code
                    for e in traiter(texte) if texte else []:
                        yield e
        except TimeoutError:
            yield Evenement("erreur", {"texte": textes.texte(langue, "erreur")})
            for e in fin("delai"):
                yield e
            return

        if lecteur is not None:
            reste_json = lecteur.terminer()
            bilan.intention = lecteur.intention or ""
            if lecteur.intention != "question":
                bilan.aiguillage = "modele"
                if lecteur.intention == "contact":
                    evts, chemin = self._evenements_carte(langue), "carte"
                elif lecteur.intention == "personnel":
                    texte_p = textes.texte(langue, "personnel")
                    evts = [Evenement("refus", {"motif": "personnel", "texte": texte_p})]
                    chemin = "refus_personnel"
                else:  # hors_sujet, ou sortie illisible : rien du modele ne s'affiche
                    texte_r = textes.texte(langue, "refus_modele")
                    evts = [Evenement("refus", {"motif": "modele", "texte": texte_r})]
                    chemin = "refus_modele"
                for e in evts + fin(chemin):
                    yield e
                return
            for e in traiter(reste_json) if reste_json else []:
                yield e

        if refus_modele or not utile(recu):
            yield Evenement(
                "refus", {"motif": "modele", "texte": textes.texte(langue, "refus_modele")}
            )
            for e in fin("refus_modele"):
                yield e
            return

        reste = (filtre.pousser(recu.lstrip()) if not demarre else "") + filtre.terminer()
        if reste:
            if bilan.premier_mot_ms is None:
                bilan.premier_mot_ms = round((time.perf_counter() - t0) * 1000, 1)
            affiche += reste
            yield Evenement("texte", {"texte": reste})
        cites = citations_valides(affiche, len(passages))
        bilan.sources_citees = len(cites)
        bilan.chiffres_remplaces = filtre.compteurs.chiffres_remplaces
        bilan.liens_retires = filtre.compteurs.liens_retires
        bilan.chiffres_non_attribues = chiffres_non_attribues(
            affiche, (passages[n - 1].morceau.texte_indexe for n in cites)
        )
        yield Evenement(
            "sources", {"citees": bool(cites), "sources": self._sources(passages, cites)}
        )
        for e in fin("reponse"):
            yield e
