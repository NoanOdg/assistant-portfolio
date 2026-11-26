"""Memoire de conversation courte, en memoire du processus seulement.

Ce qui est garde, par conversation :
- les `tours` derniers echanges (3 par defaut) : question traitee (autonome
  apres reecriture), reponse affichee apres filtres, sans renvois, tronquee a
  `caracteres_reponse`, identifiants des passages cites, marque de refus ;
- le sujet courant, l'equivalent compact d'un resume : la derniere question
  autonome qui a recu une reponse, et les projets de ses passages cites. Il
  garde le fil au-dela de la fenetre sans grossir le contexte. Un refus ne le
  change pas ; une nouvelle question le remplace.

Cle : l'identifiant de conversation porte par le jeton de session (session.py),
jamais l'adresse du visiteur. Expiration glissante : `duree_s` (30 min) apres la
derniere question ; les entrees expirees sont purgees a chaque acces. Rien
n'est ecrit sur disque, en base ni dans les journaux ; un redemarrage efface tout.

Une seule instance Cloud Run (max 1) : la memoire est exacte. A passer dans
Firestore, document par conversation avec une politique TTL sur `expire_le`,
si le service tourne un jour sur plusieurs instances (sur GKE a plusieurs
replicas, une conversation qui change de pod repart de zero).
"""

from __future__ import annotations

import re
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field

RENVOI = re.compile(r"\s*\[\d{1,3}\]")


def tronquer(texte: str, n: int) -> str:
    """Sans renvois [n] (la numerotation change d'une question a l'autre), coupe a un mot."""
    t = " ".join(RENVOI.sub("", texte).split())
    if len(t) <= n:
        return t
    coupe = t[:n].rsplit(" ", 1)[0] if " " in t[:n] else t[:n]
    return coupe.rstrip(" ,;:") + " ..."


@dataclass(frozen=True)
class Echange:
    question: str  # question traitee : autonome apres reecriture
    reponse: str  # texte affiche, apres filtres, tronque
    passages: tuple[str, ...]  # identifiants des passages cites (ou montres)
    refus: bool  # refus, carte ou erreur : ne sert pas de sujet


@dataclass(frozen=True)
class Sujet:
    question: str
    projets: tuple[str, ...] = ()  # identifiants de projet des passages cites


@dataclass
class Conversation:
    tours: int
    caracteres: int
    expire: float = 0.0
    echanges: deque[Echange] = field(default_factory=deque)
    sujet: Sujet | None = None

    def __post_init__(self) -> None:
        self.echanges = deque(self.echanges, maxlen=self.tours)

    @property
    def premier_tour(self) -> bool:
        """Aucune reponse encore : rien a reecrire, la question part telle quelle."""
        return self.sujet is None

    def sujets(self) -> list[Echange]:
        """Les echanges de la fenetre qui peuvent servir de sujet (pas les refus)."""
        return [e for e in self.echanges if not e.refus]

    def derniere(self) -> Echange | None:
        return self.echanges[-1] if self.echanges else None

    def noter(
        self,
        question: str,
        reponse: str,
        passages: tuple[str, ...],
        refus: bool,
        sujet: Sujet | None = None,
    ) -> None:
        self.echanges.append(
            Echange(question, "" if refus else tronquer(reponse, self.caracteres), passages, refus)
        )
        if not refus and sujet is not None:
            self.sujet = sujet


class Memoire:
    """Conversations par identifiant, expiration glissante, taille bornee."""

    TAILLE_MAX = 5000

    def __init__(
        self,
        tours: int = 3,
        caracteres_reponse: int = 300,
        duree_s: float = 1800,
        horloge: Callable[[], float] = time.time,
    ) -> None:
        self.tours = tours
        self.caracteres = caracteres_reponse
        self.duree_s = duree_s
        self._horloge = horloge
        self._conv: OrderedDict[str, Conversation] = OrderedDict()

    def _purger(self, maintenant: float) -> None:
        for cle in [k for k, c in self._conv.items() if c.expire < maintenant]:
            del self._conv[cle]

    def ouvrir(self, cle: str) -> Conversation:
        """La conversation de cette cle, creee si besoin ; son expiration repart."""
        maintenant = self._horloge()
        self._purger(maintenant)
        conv = self._conv.get(cle)
        if conv is None:
            while len(self._conv) >= self.TAILLE_MAX:  # les moins recemment servies partent
                self._conv.popitem(last=False)
            conv = Conversation(self.tours, self.caracteres)
            self._conv[cle] = conv
        self._conv.move_to_end(cle)
        conv.expire = maintenant + self.duree_s
        return conv

    def active(self, cle: str) -> bool:
        """Vrai si la conversation a servi depuis moins de `duree_s`."""
        self._purger(self._horloge())
        return cle in self._conv

    def __len__(self) -> int:
        return len(self._conv)
