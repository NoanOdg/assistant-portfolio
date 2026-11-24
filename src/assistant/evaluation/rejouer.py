"""Rejouer sans regenerer : nouveau juge sur des sorties enregistrees, seuil simule.

Rejuger (etude, 6, point 14) : un juge corrige puis rescelle note a nouveau les
reponses deja produites, sans rappeler le modele de redaction.

    assistant-rejuger --resultats build/comparaison/b0-r1.json --juge vertex \
        --version-juge v2 --sortie build/comparaison/b0-r1-v2.json

Simuler le filtre de seuil (point 19, fiche 3.7 : 7b contre 7c sans appel) : sur
un passage de tout_le_corpus deja joue, toute question dont le meilleur passage
est au-dessus du seuil devient un refus ecrit d'avance, au cout de l'embedding.

    assistant-simuler-seuil --resultats build/comparaison/c0-r1.json \
        --sortie build/comparaison/c0-seuil-r1.json
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import datetime as dt
import json
import sys
from pathlib import Path

from assistant import config, textes
from assistant.evaluation import jeu as jeu_mod
from assistant.evaluation.juge import Juge
from assistant.evaluation.mesures import ERREUR_TECHNIQUE, calculer, texte_rapport
from assistant.evaluation.reprise import ErreurTechnique, Reprise

ROUTES_CODE = {"carte", "refus_personnel"}


def _questions(sortie: dict, chemin: Path | None) -> list[jeu_mod.QuestionEval]:
    if chemin is None:
        brut = sortie.get("fichier_jeu") or sortie.get("jeu", "travail")
        chemin = {"travail": jeu_mod.JEU_TRAVAIL, "reserve": jeu_mod.RESERVE}.get(brut, Path(brut))
    return jeu_mod.charger(Path(chemin))


async def rejuger(
    sortie: dict,
    questions: list[jeu_mod.QuestionEval],
    juge: Juge,
    parallele: int = 4,
    reprise: Reprise | None = None,
) -> dict:
    reprise = reprise or Reprise()
    nouvelle = copy.deepcopy(sortie)
    q_par_id = {q.id: q for q in questions}
    sem = asyncio.Semaphore(parallele)

    async def une(ident: str, res: dict) -> None:
        q = q_par_id.get(ident)
        if q is None or res["chemin"] == ERREUR_TECHNIQUE:
            return
        async with sem:
            try:
                res["jugement"] = (await reprise.jouer(lambda: juge.juger(q, res))).vers_dict()
            except ErreurTechnique as e:
                res["chemin"] = ERREUR_TECHNIQUE
                res["erreur"] = f"juge : {e}"[:500]

    await asyncio.gather(*(une(i, r) for i, r in nouvelle["resultats"].items()))
    nouvelle["rejuge"] = {
        "le": dt.datetime.now(dt.UTC).isoformat(),
        "ancien_juge": sortie.get("juge"),
        "ancienne_version": sortie.get("juge_version"),
        "ancienne_empreinte": (sortie.get("empreintes") or {}).get("juge"),
    }
    nouvelle["juge"] = juge.nom
    nouvelle["juge_version"] = juge.version
    nouvelle.setdefault("empreintes", {})["juge"] = f"{juge.nom}/{juge.version}/{juge.empreinte}"
    nouvelle["mesures"] = calculer(questions, nouvelle["resultats"])
    return nouvelle


def simuler_seuil(sortie: dict, questions: list[jeu_mod.QuestionEval], seuil: float) -> dict:
    """7c simule sur un passage de C : refus ecrit d'avance au-dessus du seuil."""
    nouvelle = copy.deepcopy(sortie)
    q_par_id = {q.id: q for q in questions}
    refusees = []
    for ident, res in nouvelle["resultats"].items():
        d = res.get("meilleure_distance")
        if (
            d is None
            or d <= seuil
            or res["chemin"] in ROUTES_CODE
            or res["chemin"] == ERREUR_TECHNIQUE
            or ident not in q_par_id
        ):
            continue
        langue = res.get("langue") or q_par_id[ident].langue
        refusees.append(ident)
        res.update(
            chemin="refus_seuil",
            texte=textes.texte(langue, "hors_corpus"),
            sources=[],
            citees=False,
            carte=False,
            projets_cites=[],
            passages_cites=[],
            cout_usd=0.0,
            cout_production_usd=0.0,
            jetons_entree=0,
            jetons_sortie=0,
            jetons_cache=0,
            jetons_reflexion=0,
            premier_mot_ms=None,
            chiffres_remplaces=0,
            chiffres_non_attribues=0,
            jugement={
                "juste": False,
                "refus_correct": False,
                "injection_suivie": False,
                "motif": "refus simule par le seuil, avant le modele",
                "faits_dans_transmis": None,
            },
        )
    nouvelle["simulation"] = {
        "prefiltre_seuil": True,
        "seuil": seuil,
        "refusees": sorted(refusees),
        "source": sortie.get("_fichier", ""),
    }
    nouvelle["mesures"] = calculer(questions, nouvelle["resultats"])
    return nouvelle


def _lire(chemin: Path) -> dict:
    d = json.loads(chemin.read_text(encoding="utf-8"))
    d["_fichier"] = str(chemin)
    return d


def _ecrire(sortie: dict, chemin: Path) -> None:
    sortie.pop("_fichier", None)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(sortie, ensure_ascii=False, indent=2), encoding="utf-8")
    print(texte_rapport(sortie["mesures"]))
    print(f"\n-> {chemin}")


def main_rejuger(argv: list[str] | None = None) -> int:
    from assistant.corpus.servi import empreinte_corpus
    from assistant.evaluation.executer import construire_juge
    from assistant.fabrique import charger_corpus_brut

    p = argparse.ArgumentParser(description="Rejuge des sorties enregistrees, sans regenerer.")
    p.add_argument("--resultats", type=Path, nargs="+", required=True)
    p.add_argument("--juge", choices=["heuristique", "vertex"], default="vertex")
    p.add_argument("--modele-juge", default="gemini-3.8-flash")
    p.add_argument("--version-juge", choices=["v1", "v2"], default="v2")
    p.add_argument("--jeu", type=Path, help="jeu joue (par defaut : celui de la sortie)")
    p.add_argument("--config", type=Path, action="append", default=[])
    p.add_argument("--parallele", type=int, default=4)
    p.add_argument("--suffixe", default="-rejuge", help="ajoute au nom de chaque fichier")
    p.add_argument(
        "--forcer", action="store_true", help="accepter un corpus lu different de celui du passage"
    )
    a = p.parse_args(argv)
    r = config.charger(superpositions=a.config)
    brut, _ = charger_corpus_brut(r)
    juge = construire_juge(a, r, brut)
    for chemin in a.resultats:
        sortie = _lire(chemin)
        attendue = (sortie.get("empreintes") or {}).get("corpus_source")
        if attendue and attendue != empreinte_corpus(brut) and not a.forcer:
            raise SystemExit(
                f"{chemin} : le corpus lu a change depuis le passage ({attendue}) : "
                "rejuger contre un autre corpus changerait la reference (--forcer)"
            )
        nouvelle = asyncio.run(rejuger(sortie, _questions(sortie, a.jeu), juge, a.parallele))
        _ecrire(nouvelle, chemin.with_name(f"{chemin.stem}{a.suffixe}{chemin.suffix}"))
    return 0


def main_simuler_seuil(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Simule le filtre de seuil sur un passage de C.")
    p.add_argument("--resultats", type=Path, nargs="+", required=True)
    p.add_argument("--seuil", type=float, help="defaut : seuil des reglages pour le mode")
    p.add_argument("--jeu", type=Path)
    p.add_argument("--config", type=Path, action="append", default=[])
    p.add_argument("--suffixe", default="-seuil")
    a = p.parse_args(argv)
    seuil = (
        a.seuil if a.seuil is not None else config.charger(superpositions=a.config).seuil_distance
    )
    for chemin in a.resultats:
        sortie = _lire(chemin)
        if sortie.get("architecture") not in {"C", None} and sortie.get("mode") != "tout_le_corpus":
            raise SystemExit(f"{chemin} : la simulation du seuil vaut pour tout_le_corpus")
        nouvelle = simuler_seuil(sortie, _questions(sortie, a.jeu), seuil)
        print(f"seuil {seuil} : {len(nouvelle['simulation']['refusees'])} question(s) refusee(s)")
        _ecrire(nouvelle, chemin.with_name(f"{chemin.stem}{a.suffixe}{chemin.suffix}"))
    return 0


if __name__ == "__main__":
    sys.exit(main_rejuger())
