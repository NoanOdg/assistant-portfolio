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
    jetons_entree: int = 0
    jetons_sortie: int = 0
    cout_usd: float = 0.0
    premier_mot_ms: float | None = None
    duree_ms: float = 0.0
    chiffres_remplaces: int = 0
    liens_retires: int = 0
    sources_citees: int = 0
