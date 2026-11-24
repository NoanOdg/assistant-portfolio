"""Reprise sur erreur passagere (429, 5xx, coupure reseau), avec attente exponentielle.

Defaut D5 de l'etude : sans reprise, un passage de tout_le_corpus (35 000 jetons
par question) depasse le debit du palier et une seule exception arretait tout
le passage. Ici, une erreur passagere est rejouee apres une attente qui double
(avec un peu de hasard pour ne pas repartir tous ensemble) ; une erreur qui
persiste, ou qui n'est pas passagere, devient un `ErreurTechnique` : la question
est notee `erreur_technique`, rejouee en fin de passage, jamais comptee comme
une reponse fausse.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")

CODES_PASSAGERS = {408, 429, 500, 502, 503, 504}
NOMS_PASSAGERS = {
    "ResourceExhausted",
    "ServiceUnavailable",
    "DeadlineExceeded",
    "InternalServerError",
    "TooManyRequests",
    "ServerError",
    "ConnectError",
    "ReadTimeout",
    "ConnectTimeout",
    "RemoteProtocolError",
    "ReadError",
}


class ErreurTechnique(RuntimeError):
    """Une question n'a pas pu etre jouee : jamais comptee comme une reponse fausse."""


def code_http(exc: BaseException) -> int | None:
    for attribut in ("code", "status_code"):
        v = getattr(exc, attribut, None)
        if isinstance(v, int):
            return v
    reponse = getattr(exc, "response", None)
    v = getattr(reponse, "status_code", None)
    return v if isinstance(v, int) else None


def est_passagere(exc: BaseException) -> bool:
    """429 et 5xx (google-genai, google-api-core, httpx), coupures reseau."""
    code = code_http(exc)
    if code is not None:
        return code in CODES_PASSAGERS
    return any(t.__name__ in NOMS_PASSAGERS for t in type(exc).__mro__)


@dataclass
class Reprise:
    tentatives: int = 6
    attente_s: float = 2.0
    plafond_s: float = 60.0
    dormir: Callable[[float], Awaitable[None]] = asyncio.sleep
    hasard: Callable[[], float] = random.random

    def attente(self, essai: int) -> float:
        """Attente avant l'essai suivant : 2, 4, 8... secondes, plafonnee, +0 a 25 %."""
        base = min(self.plafond_s, self.attente_s * 2 ** (essai - 1))
        return base * (1 + 0.25 * self.hasard())

    async def jouer(self, fabrique: Callable[[], Awaitable[T]]) -> T:
        """Joue `fabrique()` ; rejoue les erreurs passageres ; sinon ErreurTechnique."""
        derniere: BaseException | None = None
        for essai in range(1, self.tentatives + 1):
            try:
                return await fabrique()
            except Exception as exc:
                derniere = exc
                if not est_passagere(exc) or essai == self.tentatives:
                    break
                await self.dormir(self.attente(essai))
        raise ErreurTechnique(f"{type(derniere).__name__}: {derniere}") from derniere
