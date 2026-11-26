"""Types partages : morceaux du corpus, passages trouves, evenements du flux."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Langue = Literal["fr", "en"]
LANGUES: tuple[str, ...] = ("fr", "en")


def empreinte(texte: str) -> str:
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Section:
    """Une section de page publique, avant decoupage."""

    titre_page: str
    chemin: tuple[str, ...]  # titres h2 puis h3
    ancre: str
    texte: str
    type: str = "prose"  # prose, fiche, metrique, parcours


@dataclass(frozen=True)
class Page:
    url: str  # chemin public, par exemple /projets/insei ou /en/projets/insei
    langue: str
    projet: str  # identifiant du projet, ou "parcours"
    titre: str
    sections: tuple[Section, ...]


@dataclass(frozen=True)
class Morceau:
    id: str
    url: str
    ancre: str
    titre: str
    contexte: str  # en-tete de contexte, par exemple "Hydre > Comment le systeme decide"
    texte: str
    langue: str
    projet: str
    type: str
    empreinte: str

    @property
    def lien(self) -> str:
        return f"{self.url}#{self.ancre}" if self.ancre else self.url

    @property
    def texte_indexe(self) -> str:
        """Ce qui est vectorise et lu par le modele : contexte puis texte."""
        return f"{self.contexte}\n{self.texte}"

    def vers_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def depuis_dict(cls, d: dict[str, Any]) -> Morceau:
        return cls(**{k: d[k] for k in cls.__dataclass_fields__})


@dataclass(frozen=True)
class Passage:
    morceau: Morceau
    distance: float  # distance cosinus, 0 = identique


@dataclass
class Evenement:
    """Un evenement SSE. `type` devient la ligne `event:`."""

    type: str
    donnees: dict[str, Any] = field(default_factory=dict)


@dataclass
class Bilan:
    """Ce qui est journalise pour une question. Jamais d'IP ni de texte."""

    chemin: str = ""
    passages: int = 0
    meilleure_distance: float | None = None
    jetons_entree: int = 0  # entree facturee, jetons lus dans le cache compris
    jetons_sortie: int = 0  # reponse, sans la reflexion
    jetons_cache: int = 0  # part de l'entree lue dans le cache implicite
    jetons_reflexion: int = 0  # facturee au prix de la sortie
    cout_usd: float = 0.0  # cout reel, remise du cache comprise
    cout_production_usd: float = 0.0  # meme question, sans cache (plafond de la regle R0)
    premier_mot_ms: float | None = None
    duree_ms: float = 0.0
    chiffres_remplaces: int = 0
    liens_retires: int = 0
    sources_citees: int = 0
    # Chiffres affiches absents de tous les passages cites (mesure 10c, non bloquante).
    chiffres_non_attribues: int = 0
    appels_modele: int = 0
    appels_outils: int = 0  # branche A seulement
    # Qui a aiguille : "code" (mots-cles), "modele" (etiquette en sortie structuree).
    aiguillage: str = ""
    intention: str = ""  # etiquette lue dans la sortie du modele, si demandee
    # Memoire de conversation : "" (premier tour, ou memoire coupee), suite,
    # nouveau_sujet, transformation, comparaison, repli (question d'origine
    # apres une reecriture refusee ou en echec). Hors liste blanche du journal.
    memoire: str = ""
    reecriture_ms: float | None = None
    jetons_reecriture: int = 0  # entree et sortie de l'appel de reecriture
    cout_reecriture_usd: float = 0.0  # compris dans cout_usd
