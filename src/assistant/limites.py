"""Limites : par visiteur (memoire) et plafond quotidien global (memoire ou Firestore).

Cle d'un visiteur : HMAC de son adresse avec un sel tire au hasard chaque jour,
garde en memoire seulement. L'adresse n'est jamais ecrite, ni en journal ni en base.
Le compteur en memoire n'est exact qu'avec une seule instance (Cloud Run, max 1).
Sur GKE avec plusieurs replicas, chaque pod compte pour lui : le plafond global,
lui, reste exact parce qu'il vit dans Firestore.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets
import time
from collections import deque
from collections.abc import Callable
from typing import Protocol


def aujourdhui() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")


class LimiteurVisiteur:
    TAILLE_MAX = 20_000

    def __init__(
        self,
        par_minute: int,
        par_jour: int,
        horloge: Callable[[], float] = time.monotonic,
        jour: Callable[[], str] = aujourdhui,
    ) -> None:
        self.par_minute = par_minute
        self.par_jour = par_jour
        self._horloge = horloge
        self._jour = jour
        self._jour_courant = ""
        self._sel = b""
        self._minute: dict[str, deque[float]] = {}
        self._quotidien: dict[str, int] = {}

    def _tourner(self) -> None:
        j = self._jour()
        if j != self._jour_courant:
            self._jour_courant = j
            self._sel = secrets.token_bytes(32)
            self._minute.clear()
            self._quotidien.clear()

    def cle(self, adresse: str) -> str:
        self._tourner()
        return hmac.new(self._sel, adresse.encode(), hashlib.sha256).hexdigest()[:20]

    def autoriser(self, adresse: str) -> str | None:
        """None si la question passe, sinon le motif du refus ('minute' ou 'jour')."""
        cle = self.cle(adresse)
        maintenant = self._horloge()
        fenetre = self._minute.setdefault(cle, deque())
        while fenetre and maintenant - fenetre[0] >= 60:
            fenetre.popleft()
        if len(fenetre) >= self.par_minute:
            return "minute"
        if self._quotidien.get(cle, 0) >= self.par_jour:
            return "jour"
        fenetre.append(maintenant)
        self._quotidien[cle] = self._quotidien.get(cle, 0) + 1
        if len(self._minute) > self.TAILLE_MAX:
            for k in [k for k, f in self._minute.items() if not f][: self.TAILLE_MAX // 2]:
                del self._minute[k]
        return None


class CompteurQuotidien(Protocol):
    async def prendre(self, nom: str, plafond: int) -> bool: ...


class CompteurMemoire:
    def __init__(self, jour: Callable[[], str] = aujourdhui) -> None:
        self._jour = jour
        self.valeurs: dict[tuple[str, str], int] = {}

    async def prendre(self, nom: str, plafond: int) -> bool:
        cle = (self._jour(), nom)
        if self.valeurs.get(cle, 0) >= plafond:
            return False
        self.valeurs[cle] = self.valeurs.get(cle, 0) + 1
        return True


class CompteurFirestore:
    """compteurs/{jour}-{nom}, incremente en transaction. Survit aux redemarrages."""

    def __init__(self, projet: str) -> None:
        from google.cloud import firestore

        self._db = firestore.AsyncClient(project=projet)
        self._firestore = firestore

    async def prendre(self, nom: str, plafond: int) -> bool:
        ref = self._db.collection("compteurs").document(f"{aujourdhui()}-{nom}")
        expire = dt.datetime.now(dt.UTC) + dt.timedelta(days=60)

        @self._firestore.async_transactional
        async def incrementer(tx) -> bool:
            snap = await ref.get(transaction=tx)
            n = (snap.get("n") if snap.exists else 0) or 0
            if n >= plafond:
                return False
            tx.set(ref, {"n": n + 1, "expire_le": expire})
            return True

        return await incrementer(self._db.transaction())
