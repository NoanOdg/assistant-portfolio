"""Jeu d'evaluation : chargement, validation, sceau de la reserve et du juge."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from assistant.config import RACINE

DOSSIER = RACINE / "evaluation"
JEU_TRAVAIL = DOSSIER / "jeu" / "questions.yaml"
RESERVE = DOSSIER / "jeu" / "reserve-scellee.yaml"
# Brouillons du jeu v2 : ni scelles ni joues par la CI tant que Noan ne les a pas valides.
JEU_V2_BROUILLON = DOSSIER / "jeu" / "questions-v2-brouillon.yaml"
RESERVE_V2_BROUILLON = DOSSIER / "jeu" / "reserve-v2-brouillon.yaml"
SCEAU = DOSSIER / "jeu" / "sceau.json"
CONSIGNE_JUGE = DOSSIER / "juge" / "consigne.md"

FAMILLES = {
    "fait_chiffre",
    "description",
    "transversale",
    "contact",
    "hors_sujet",
    "personnel",
    "opinion",
    "injection",
    "fausse_premisse",
    "competence_absente",
    "renseignement",  # disponibilite, contrat, lieu, langues, formation
}
ATTENDUS = {"repondre", "carte", "refuser"}


class SceauRompu(RuntimeError):
    pass


@dataclass(frozen=True)
class QuestionEval:
    id: str
    langue: str
    famille: str
    question: str
    attendu: str
    reponse_attendue: str
    pages: tuple[str, ...] = ()
    chiffres: tuple[str, ...] = ()
    motifs_interdits: tuple[str, ...] = field(default_factory=tuple)
    # Faits cles attendus, courts : le juge verifie leur presence, pas une phrase modele.
    faits: tuple[str, ...] = ()
    a_valider: bool = False

    @property
    def a_repondre(self) -> bool:
        return self.attendu in {"repondre", "carte"}

    @property
    def faits_cles(self) -> tuple[str, ...]:
        """Faits cles, ou la phrase de reference des anciens jeux (reserve scellee v1)."""
        if self.faits:
            return self.faits
        return (self.reponse_attendue,) if self.reponse_attendue else ()


def empreinte_fichier(chemin: Path) -> str:
    # Fins de ligne normalisees : le sceau ne depend pas de la plateforme.
    return hashlib.sha256(chemin.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def charger(chemin: Path) -> list[QuestionEval]:
    brut = yaml.safe_load(chemin.read_text(encoding="utf-8")) or []
    questions = []
    for d in brut:
        q = QuestionEval(
            id=str(d["id"]),
            langue=d["langue"],
            famille=d["famille"],
            question=str(d["question"]).strip(),
            attendu=d["attendu"],
            reponse_attendue=str(d.get("reponse_attendue", "")).strip(),
            pages=tuple(d.get("pages") or ()),
            chiffres=tuple(map(str, d.get("chiffres") or ())),
            motifs_interdits=tuple(d.get("motifs_interdits") or ()),
            faits=tuple(str(f).strip() for f in d.get("faits") or ()),
            a_valider=bool(d.get("a_valider", False)),
        )
        if q.langue not in {"fr", "en"} or q.famille not in FAMILLES or q.attendu not in ATTENDUS:
            raise ValueError(f"question {q.id} invalide")
        if q.attendu == "repondre" and not q.pages:
            raise ValueError(f"question {q.id} : pages attendues manquantes")
        if q.attendu == "repondre" and not q.faits_cles:
            raise ValueError(f"question {q.id} : faits attendus manquants")
        questions.append(q)
    ids = [q.id for q in questions]
    if len(ids) != len(set(ids)):
        raise ValueError(f"identifiants en double dans {chemin.name}")
    return questions


def lire_sceau() -> dict:
    return json.loads(SCEAU.read_text(encoding="utf-8"))


def verifier_reserve() -> str:
    attendu = lire_sceau()["reserve_sha256"]
    reel = empreinte_fichier(RESERVE)
    if reel != attendu:
        raise SceauRompu("la reserve scellee a ete modifiee : son empreinte ne correspond plus")
    return reel


def consigne_juge() -> str:
    texte = CONSIGNE_JUGE.read_text(encoding="utf-8")
    if empreinte_fichier(CONSIGNE_JUGE) != lire_sceau()["consigne_juge_sha256"]:
        raise SceauRompu("la consigne du juge a change : elle est gelee par empreinte")
    return texte
