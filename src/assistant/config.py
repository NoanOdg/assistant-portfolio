"""Lecture des reglages : TOML versionne, remplace par l'environnement."""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RACINE = Path(os.environ.get("ASSISTANT_RACINE", Path(__file__).resolve().parents[2]))
FICHIER_DEFAUT = RACINE / "config" / "reglages.toml"


def _convertir(brut: str, modele: Any) -> Any:
    """Convertit une variable d'environnement dans le type de la valeur TOML."""
    if isinstance(modele, bool):
        return brut.strip().lower() in {"1", "true", "oui", "vrai", "yes"}
    if isinstance(modele, int):
        return int(brut)
    if isinstance(modele, float):
        return float(brut)
    if isinstance(modele, list):
        return (
            json.loads(brut)
            if brut.strip().startswith("[")
            else [x.strip() for x in brut.split(",") if x.strip()]
        )
    return brut


def charger_brut(chemin: Path | None = None, env: dict[str, str] | None = None) -> dict:
    env = dict(os.environ) if env is None else env
    chemin = Path(env.get("ASSISTANT_REGLAGES", chemin or FICHIER_DEFAUT))
    with chemin.open("rb") as f:
        donnees = tomllib.load(f)
    for section, valeurs in donnees.items():
        for cle, valeur in valeurs.items():
            nom = f"ASSISTANT_{section}_{cle}".upper()
            if nom in env:
                valeurs[cle] = _convertir(env[nom], valeur)
    return donnees


@dataclass(frozen=True)
class Reglages:
    moteur: dict
    corpus: dict
    recherche: dict
    limites: dict
    securite: dict
    journal: dict
    tarifs: dict
    site: dict
    secrets: dict = field(default_factory=dict, repr=False)

    @property
    def mode(self) -> str:
        return self.moteur["mode"]

    @property
    def seuil_distance(self) -> float:
        cle = "seuil_distance_vertex" if self.mode == "vertex" else "seuil_distance_local"
        return float(self.recherche[cle])

    def verifier(self) -> None:
        """Refuse un demarrage incoherent plutot qu'un service qui repond mal."""
        if self.mode not in {"local", "vertex"}:
            raise ValueError(f"mode inconnu : {self.mode}")
        if not 0 < self.seuil_distance < 2:
            raise ValueError(
                "seuil de distance non cale pour ce mode : lancer assistant-caler-seuil"
            )
        if self.mode == "vertex":
            if not self.moteur["projet_gcp"]:
                raise ValueError("ASSISTANT_MOTEUR_PROJET_GCP manquant")
            if not self.secrets.get("session"):
                raise ValueError("secret de session manquant (ASSISTANT_SECRET_SESSION)")
            if self.securite["anti_robot"] == "turnstile" and not self.secrets.get("turnstile"):
                raise ValueError("secret Turnstile manquant (ASSISTANT_SECRET_TURNSTILE)")


def charger(chemin: Path | None = None, env: dict[str, str] | None = None) -> Reglages:
    env = dict(os.environ) if env is None else env
    d = charger_brut(chemin, env)
    secrets = {
        "session": env.get("ASSISTANT_SECRET_SESSION", ""),
        "turnstile": env.get("ASSISTANT_SECRET_TURNSTILE", ""),
    }
    return Reglages(
        **{
            k: d[k]
            for k in (
                "moteur",
                "corpus",
                "recherche",
                "limites",
                "securite",
                "journal",
                "tarifs",
                "site",
            )
        },
        secrets=secrets,
    )
