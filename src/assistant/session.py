"""Anti-robots et jeton de session.

1. Au premier envoi, le navigateur obtient un jeton Turnstile ; POST /session le
   verifie aupres de Cloudflare et rend un jeton de session signe (HMAC, 30 min).
2. Chaque question porte ce jeton. Aucun cookie.
3. L'evaluation de la CI presente un jeton d'identite Google du compte de
   deploiement : elle passe sans Turnstile et sur un plafond a part.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Protocol

import httpx


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
    async def verifier(self, jeton: str) -> bool: ...


class AntiRobotDesactive:
    """Poste local et tests seulement."""

    async def verifier(self, jeton: str) -> bool:
        return True


class Turnstile:
    URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

    def __init__(self, secret: str, client: httpx.AsyncClient | None = None) -> None:
        self._secret = secret
        self._client = client or httpx.AsyncClient(timeout=5)

    async def verifier(self, jeton: str) -> bool:
        # L'adresse du visiteur n'est volontairement pas transmise (champ facultatif).
        r = await self._client.post(self.URL, data={"secret": self._secret, "response": jeton})
        return r.status_code == 200 and bool(r.json().get("success"))


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
