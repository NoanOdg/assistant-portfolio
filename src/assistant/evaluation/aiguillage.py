"""Banc d'aiguillage du code (etude, fiche 3.6), a part du jeu d'evaluation.

60 tournures ecrites depuis les classes d'intention (contact, personnel,
question, hors_sujet), fautes comprises : la moitie "correction" sert a corriger
les mots-cles, la moitie "verification" n'est lue qu'apres, pour verifier. Rien
n'appelle de modele : le banc est gratuit et se rejoue a chaque changement.

    assistant-aiguillage                       # v1 et classes, moitie correction
    assistant-aiguillage --moitie verification --jeu evaluation/jeu/questions.yaml

Regle de la fiche : "classes" remplace "v1" si le rappel sur personnel et contact
monte sans aucun faux positif sur les questions de travail du jeu.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from assistant.evaluation import jeu as jeu_mod
from assistant.garde import aiguiller

CLASSES = ("contact", "personnel", "question", "hors_sujet")
ROUTES = {
    "contact": {"contact", "telephone"},
    "personnel": {"personnel"},
    "question": {"recherche"},
    "hors_sujet": {"recherche"},
}
MOITIES = ("correction", "verification")
# Familles du jeu dont la question doit suivre son cours (jamais aiguillee par le code).
FAMILLES_TRAVAIL = {"fait_chiffre", "description", "transversale", "renseignement"}


@dataclass(frozen=True)
class Tournure:
    id: str
    langue: str
    classe: str
    texte: str
    moitie: str


def charger_banc(chemin: Path | None = None) -> list[Tournure]:
    brut = yaml.safe_load((chemin or jeu_mod.BANC_AIGUILLAGE).read_text(encoding="utf-8")) or []
    banc = []
    for d in brut:
        t = Tournure(str(d["id"]), d["langue"], d["classe"], str(d["texte"]), d["moitie"])
        if t.classe not in CLASSES or t.moitie not in MOITIES or t.langue not in {"fr", "en"}:
            raise ValueError(f"tournure {t.id} invalide")
        banc.append(t)
    if len({t.id for t in banc}) != len(banc):
        raise ValueError("identifiants en double dans le banc d'aiguillage")
    return banc


def mesurer(banc: list[Tournure], version: str, moitie: str | None = "correction") -> dict:
    choisies = [t for t in banc if moitie is None or t.moitie == moitie]
    par_classe: dict[str, dict] = {}
    erreurs = []
    for c in CLASSES:
        lot = [t for t in choisies if t.classe == c]
        bonnes = 0
        for t in lot:
            route = aiguiller(t.texte, version)
            if route in ROUTES[c]:
                bonnes += 1
            else:
                erreurs.append({"id": t.id, "classe": c, "route": route, "texte": t.texte})
        par_classe[c] = {"bonnes": bonnes, "effectif": len(lot)}
    rappel = sum(par_classe[c]["bonnes"] for c in ("contact", "personnel"))
    faux_positifs = sum(
        par_classe[c]["effectif"] - par_classe[c]["bonnes"] for c in ("question", "hors_sujet")
    )
    return {
        "version": version,
        "moitie": moitie or "tout",
        "classes": par_classe,
        "rappel_contact_personnel": rappel,
        "faux_positifs": faux_positifs,
        "erreurs": erreurs,
    }


def faux_positifs_jeu(questions: list[jeu_mod.QuestionEval], version: str) -> list[str]:
    """Questions de travail du jeu aiguillees a tort par le code (refus ou carte)."""
    return [
        q.id
        for q in questions
        if q.attendu == "repondre"
        and q.famille in FAMILLES_TRAVAIL
        and aiguiller(q.question, version) != "recherche"
    ]


def verdict(v1: dict, classes: dict, fp_jeu: list[str]) -> str:
    monte = classes["rappel_contact_personnel"] > v1["rappel_contact_personnel"]
    aucune_baisse = all(
        classes["classes"][c]["bonnes"] >= v1["classes"][c]["bonnes"]
        for c in ("contact", "personnel")
    )
    if monte and aucune_baisse and not fp_jeu:
        return "classes remplace v1 (rappel en hausse, aucun faux positif sur le jeu)"
    raisons = []
    if not monte:
        raisons.append("le rappel ne monte pas")
    if not aucune_baisse:
        raisons.append("une classe baisse")
    if fp_jeu:
        raisons.append(f"faux positifs sur le jeu : {', '.join(fp_jeu)}")
    return "v1 reste (" + "; ".join(raisons) + ")"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Banc d'aiguillage du code, sans modele.")
    p.add_argument("--banc", type=Path)
    p.add_argument("--moitie", choices=[*MOITIES, "tout"], default="correction")
    p.add_argument(
        "--jeu", type=Path, help="jeu dont les questions de travail ne doivent pas etre aiguillees"
    )
    a = p.parse_args(argv)
    banc = charger_banc(a.banc)
    moitie = None if a.moitie == "tout" else a.moitie
    res = {v: mesurer(banc, v, moitie) for v in ("v1", "classes")}
    for v, m in res.items():
        detail = ", ".join(f"{c} {d['bonnes']}/{d['effectif']}" for c, d in m["classes"].items())
        print(f"{v} ({m['moitie']}) : {detail} ; faux positifs {m['faux_positifs']}")
        if moitie != "verification":  # la moitie gardee ne montre pas ses erreurs
            for e in m["erreurs"]:
                print(f"  - {e['id']} [{e['classe']} -> {e['route']}] {e['texte']}")
    fp = faux_positifs_jeu(jeu_mod.charger(a.jeu), "classes") if a.jeu else []
    if a.jeu:
        print(f"faux positifs de classes sur le jeu : {len(fp)} {fp if fp else ''}")
    print(verdict(res["v1"], res["classes"], fp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
