"""Porte de la CI : echoue (code 1) si une mesure passe sous son seuil.

    assistant-porte --resultats build/eval-candidate.json --profil production \
        --reference evaluation/reference.json

Les seuils vivent dans evaluation/seuils.toml, ecrits avant la premiere mesure.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

from assistant.evaluation.jeu import DOSSIER

SEUILS = DOSSIER / "seuils.toml"

# cle du seuil -> (mesure, sens, nature)
REGLES = {
    "injections_suivies_max": ("injections_suivies", "max_compte"),
    "refus_personnel_min": ("refus_personnel", "min_taux"),
    "reponses_sourcees_min": ("reponses_sourcees", "min_taux"),
    "chiffres_fideles_min": ("chiffres_fideles", "min_taux"),
    "cartes_contact_min": ("cartes_contact", "min_taux"),
    "justes_min": ("justes", "min_taux"),
    "refus_reussis_min": ("refus_reussis", "min_taux"),
    "refus_a_tort_max": ("refus_a_tort", "max_taux"),
}


def verifier(mesures: dict, seuils: dict, reference: dict | None = None) -> list[str]:
    """Liste des echecs, vide si la porte s'ouvre."""
    echecs = []
    m = mesures["mesures"]
    for cle, seuil in seuils.items():
        if cle not in REGLES:
            continue
        nom, sens = REGLES[cle]
        d = m[nom]
        if sens == "max_compte":
            if d["succes"] > seuil:
                echecs.append(f"{nom} : {d['succes']} > {seuil}")
            continue
        if not d["effectif"]:
            echecs.append(f"{nom} : effectif nul, la mesure n'a pas eu lieu")
            continue
        taux = d["succes"] / d["effectif"]
        if sens == "min_taux" and taux < seuil:
            echecs.append(f"{nom} : {d['succes']} sur {d['effectif']} ({taux:.1%}) < {seuil:.1%}")
        if sens == "max_taux" and taux > seuil:
            echecs.append(f"{nom} : {d['succes']} sur {d['effectif']} ({taux:.1%}) > {seuil:.1%}")
    if reference is not None and "marge_justes" in seuils:
        avant = reference["mesures"]["justes"]["succes"]
        apres = m["justes"]["succes"]
        if avant - apres > seuils["marge_justes"]:
            echecs.append(
                f"non-regression : {apres} justes contre {avant} dans la reference "
                f"(marge {seuils['marge_justes']})"
            )
    return echecs


def charger_seuils(profil: str, chemin: Path = SEUILS) -> dict:
    with chemin.open("rb") as f:
        return tomllib.load(f)["profils"][profil]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Porte de qualite de la CI.")
    p.add_argument("--resultats", type=Path, required=True)
    p.add_argument("--profil", default="production")
    p.add_argument("--seuils", type=Path, default=SEUILS)
    p.add_argument("--reference", type=Path)
    a = p.parse_args(argv)
    mesures = json.loads(a.resultats.read_text(encoding="utf-8"))["mesures"]
    reference = None
    if a.reference and a.reference.exists():
        reference = json.loads(a.reference.read_text(encoding="utf-8"))["mesures"]
    echecs = verifier(mesures, charger_seuils(a.profil, a.seuils), reference)
    if echecs:
        print("PORTE FERMEE")
        for e in echecs:
            print(f"- {e}")
        return 1
    print(f"PORTE OUVERTE (profil {a.profil}, {mesures['effectif']} questions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
