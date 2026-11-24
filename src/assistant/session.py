"""Anti-robots et jeton de session.

1. Le navigateur demande un defi ALTCHA (GET /defi) : preuve de travail signee
   par HMAC, qui expire vite. Il la resout, puis POST /session verifie la
   solution (signature, expiration, calcul, une seule utilisation) et rend un
   jeton de session signe (HMAC, 30 min). Aucun tiers : le script du widget est
   servi par le site, la verification est locale.
2. Chaque question porte ce jeton. Aucun cookie.
3. L'evaluation de la CI presente un jeton d'identite Google du compte de
   deploiement : elle passe sans ALTCHA et sur un plafond a part.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Protocol

import altcha


class JetonInvalide(Exception):
    pass


class Sessions:
    def __init__(self, cle: bytes, duree_s: int = 1800) -> None:
        if len(cle) < 16:
            raise ValueError("cle de session trop courte")
        self._cle = cle
        self.duree_s = duree_s

    def emettre(self, maintenant: float | None = None) -> str:
        charge = {"exp": int((maintenant or time.time()) + self.duree_s), "n": secrets.token_hex(8)}
        corps = base64.urlsafe_b64encode(json.dumps(charge).encode()).decode().rstrip("=")
        sig = hmac.new(self._cle, corps.encode(), hashlib.sha256).hexdigest()
        return f"{corps}.{sig}"

    def verifier(self, jeton: str, maintenant: float | None = None) -> None:
        try:
            corps, sig = jeton.split(".", 1)
        except ValueError as e:
            raise JetonInvalide("forme") from e
        attendu = hmac.new(self._cle, corps.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, attendu):
            raise JetonInvalide("signature")
        charge = json.loads(base64.urlsafe_b64decode(corps + "=" * (-len(corps) % 4)))
        if charge["exp"] < (maintenant or time.time()):
            raise JetonInvalide("expire")


class AntiRobot(Protocol):
    def defi(self) -> dict: ...

    async def verifier(self, charge: str) -> str | None:
        """None si la solution est acceptee, sinon le motif du refus."""
        ...


class RegistreSolutions:
    """Defis deja presentes, par signature, jusqu'a leur expiration (memoire).

    Une seule instance Cloud Run : le registre est exact. Sur GKE a plusieurs
    replicas, chaque pod a le sien (voir README, ecarts). Au-dela de la taille
    maximale, les plus anciens partent : ils sont aussi les plus proches de
    l'expiration, puisque tous les defis ont la meme duree.
    """

    TAILLE_MAX = 50_000

    def __init__(self, horloge: Callable[[], float] = time.time) -> None:
        self._horloge = horloge
        self._vus: OrderedDict[str, float] = OrderedDict()

    def _purger(self) -> None:
        maintenant = self._horloge()
        while self._vus and next(iter(self._vus.values())) < maintenant:
            self._vus.popitem(last=False)
        while len(self._vus) > self.TAILLE_MAX:
            self._vus.popitem(last=False)

    def reserver(self, signature: str, jusqu_a: float) -> bool:
        """False si la signature a deja ete presentee. Verifier et inscrire se
        font sans attente entre les deux : deux envois simultanes ne passent pas."""
        self._purger()
        if signature in self._vus:
            return False
        self._vus[signature] = jusqu_a
        return True

    def liberer(self, signature: str) -> None:
        self._vus.pop(signature, None)

    def __len__(self) -> int:
        return len(self._vus)


class Altcha:
    """Defi ALTCHA (preuve de travail v2 de la bibliotheque officielle `altcha`).

    Le visiteur cherche un compteur dont la cle derivee (PBKDF2/SHA-256 par
    defaut) commence par le prefixe demande. Les parametres sont signes par
    HMAC-SHA-256 avec la cle de Secret Manager : un defi ne peut etre ni forge
    ni modifie. Un defi presente une fois est grille, solution juste ou fausse :
    le service ne sert pas a resoudre des defis a la place d'un robot.
    """

    def __init__(
        self,
        cle: str | bytes,
        algorithme: str = "PBKDF2/SHA-256",
        cout: int = 5000,
        prefixe: str = "00",
        expiration_s: int = 120,
        horloge: Callable[[], float] = time.time,
    ) -> None:
        if len(cle) < 16:
            raise ValueError("cle ALTCHA trop courte")
        self._cle = cle
        self.algorithme = algorithme
        self.cout = cout
        self.prefixe = prefixe
        self.expiration_s = expiration_s
        self._horloge = horloge
        self.registre = RegistreSolutions(horloge)

    def defi(self) -> dict:
        return altcha.create_challenge(
            algorithm=self.algorithme,
            cost=self.cout,
            key_prefix=self.prefixe,
            expires_at=int(self._horloge()) + self.expiration_s,
            hmac_secret=self._cle,
        ).to_dict()

    async def verifier(self, charge: str) -> str | None:
        try:
            signature = json.loads(base64.b64decode(charge, validate=True))["challenge"][
                "signature"
            ]
        except (ValueError, KeyError, TypeError, RecursionError):
            return "invalide"
        if not isinstance(signature, str) or not signature:
            return "invalide"
        # Marge d'une seconde : l'expiration du defi est un entier de secondes.
        if not self.registre.reserver(signature, self._horloge() + self.expiration_s + 1):
            return "rejoue"
        # La derivation (quelques millisecondes) quitte la boucle d'evenements.
        resultat = await asyncio.to_thread(altcha.verify_solution, charge, self._cle)
        if resultat.verified:
            return None
        if resultat.expired:
            self.registre.liberer(signature)
            return "expire"
        if resultat.invalid_signature is not False:
            # Signature fausse ou charge illisible : rien a griller.
            self.registre.liberer(signature)
        return "invalide"


class AntiRobotDesactive:
    """Poste local et tests seulement : le defi est servi, toute solution passe."""

    def __init__(self) -> None:
        self._altcha = Altcha(secrets.token_bytes(32), cout=1, prefixe="")

    def defi(self) -> dict:
        return self._altcha.defi()

    async def verifier(self, charge: str) -> str | None:
        return None


class IdentiteEvaluation(Protocol):
    def verifier(self, jeton: str) -> bool: ...


class IdentiteRefusee:
    def verifier(self, jeton: str) -> bool:
        return False


class IdentiteGoogle:
    """Jeton d'identite Google : signature, audience (URL du service) et compte attendu."""

    def __init__(self, audience: str, compte: str) -> None:
        self.audience = audience
        self.compte = compte

    def verifier(self, jeton: str) -> bool:
        if not self.compte:
            return False
        from google.auth.transport import requests as transport
        from google.oauth2 import id_token

        try:
            infos = id_token.verify_oauth2_token(jeton, transport.Request(), audience=self.audience)
        except ValueError:
            return False
        return infos.get("email") == self.compte and bool(infos.get("email_verified"))
