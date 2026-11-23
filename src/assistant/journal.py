"""Journaux JSON pour Cloud Logging, sur liste blanche de champs.

Ni adresse IP, ni navigateur, ni texte de question : un champ hors liste est
retire avant ecriture, quel que soit l'appelant. Le journal d'acces d'uvicorn
(qui ecrit l'adresse du client) est coupe au lancement (--no-access-log).
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

CHAMPS = {
    "trace",
    "chemin",
    "langue",
    "passages",
    "meilleure_distance",
    "jetons_entree",
    "jetons_sortie",
    "cout_usd",
    "premier_mot_ms",
    "duree_ms",
    "chiffres_remplaces",
    "liens_retires",
    "sources_citees",
    "appels_modele",
    "appels_outils",
    "branche",
    "modele",
    "embeddings",
    "corpus",
    "consigne",
    "motif",
    "statut",
    "evaluation",
    "version",
}
NOM = "assistant"


class FormatJson(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entree: dict[str, Any] = {
            "severity": record.levelname,
            "message": record.getMessage(),
        }
        champs = getattr(record, "champs", {}) or {}
        entree.update({k: v for k, v in champs.items() if k in CHAMPS})
        if champs.get("trace_gcp"):
            entree["logging.googleapis.com/trace"] = champs["trace_gcp"]
        if record.exc_info:
            entree["pile"] = self.formatException(record.exc_info)
        return json.dumps(entree, ensure_ascii=False)


class SortieStandard(logging.StreamHandler):
    """Ecrit toujours dans le sys.stdout courant (utile aux tests qui le capturent)."""

    @property
    def stream(self):
        return sys.stdout

    @stream.setter
    def stream(self, _valeur) -> None:
        pass


def configurer(niveau: int = logging.INFO) -> logging.Logger:
    journal = logging.getLogger(NOM)
    if not any(isinstance(h.formatter, FormatJson) for h in journal.handlers):
        gestionnaire = SortieStandard()
        gestionnaire.setFormatter(FormatJson())
        journal.addHandler(gestionnaire)
    journal.setLevel(niveau)
    journal.propagate = False
    return journal


def ecrire(message: str, **champs: Any) -> None:
    logging.getLogger(NOM).info(message, extra={"champs": champs})
