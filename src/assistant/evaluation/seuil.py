"""Calage du seuil de refus, sur le jeu de travail seulement (jamais la reserve).

Regle (reprise de la V2 de l'INSEI) : le seuil le plus bas qui n'ecarte pas plus
de 5 % des questions a repondre. La distance est arrondie au millionieme par
exces, car un arrondi au plus proche ecarterait une question de plus. Le
chiffre est affiche, jamais ecrit dans la configuration : un humain le recopie.

    assistant-caler-seuil
"""

from __future__ import annotations

import argparse
import asyncio
import math
from pathlib import Path

from assistant import config
from assistant.evaluation import jeu as jeu_mod


def caler(distances_a_repondre: list[float], part_ecartee: float = 0.05) -> float:
    if not distances_a_repondre:
        raise ValueError("aucune question a repondre")
    v = sorted(distances_a_repondre)
    garder = math.ceil((1 - part_ecartee) * len(v))
    return math.ceil(v[garder - 1] * 1e6) / 1e6


async def distances(
    questions: list[jeu_mod.QuestionEval], superpositions: list[Path] | None = None
) -> dict[str, float]:
    from assistant.fabrique import charger_corpus, construire_recherche

    r = config.charger(superpositions=superpositions)
    morceaux, _ = charger_corpus(r)
    embedder, recherche = await construire_recherche(r, morceaux)
    res = {}
    for q in questions:
        v = (await embedder.vectoriser([q.question], "question"))[0]
        p = await recherche.chercher(v, q.langue, 1)
        res[q.id] = p[0].distance if p else 2.0
    return res


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--fichier-jeu", type=Path, default=jeu_mod.JEU_TRAVAIL)
    p.add_argument("--config", type=Path, action="append", default=[])
    a = p.parse_args(argv)
    if a.fichier_jeu.resolve() == jeu_mod.RESERVE.resolve():
        raise SystemExit("le seuil se cale sur le jeu de travail, jamais sur la reserve")
    questions = [q for q in jeu_mod.charger(a.fichier_jeu) if q.attendu != "carte"]
    d = asyncio.run(distances(questions, a.config))
    seuil = caler([d[q.id] for q in questions if q.attendu == "repondre"])
    refuser = [q for q in questions if q.attendu == "refuser"]
    ecartees = sum(1 for q in refuser if d[q.id] > seuil)
    a_tort = sum(1 for q in questions if q.attendu == "repondre" and d[q.id] > seuil)
    print(f"seuil propose : {seuil:.6f}")
    print(f"a refuser ecartees par le code : {ecartees} sur {len(refuser)}")
    print(
        f"a repondre ecartees a tort : {a_tort} sur "
        f"{sum(1 for q in questions if q.attendu == 'repondre')}"
    )
    print("A recopier a la main dans config/reglages.toml (seuil_distance_vertex).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
