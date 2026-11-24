"""Passages d'or et rappel hors ligne (etude, plan 5.2, etape 0.1 ; 6, point 17).

Passages d'or : le juge lit tout le corpus de la langue et designe, pour chaque
fait cle des questions a repondre, les sections qui le portent. Noan en
controle 15 (tirees au hasard, graine fixe), puis le fichier est verse dans
evaluation/or/passages-or.json.

    assistant-or --fichier-jeu evaluation/jeu/questions.yaml --sortie build/passages-or.json

Rappel hors ligne, sans modele de redaction, pour comparer des variantes
d'embedding, de decoupage ou de recherche (fiches 3.2 a 3.5) : rappel@k (tous
les faits couverts, ou au moins un), rang moyen reciproque, et aire sous la
courbe ROC de la distance du meilleur passage entre questions a repondre et a
refuser (utile au seuil). Index en memoire : aucune version Firestore par variante.

    assistant-rappel --or evaluation/or/passages-or.json \
        --fichier-jeu evaluation/jeu/questions.yaml --config evaluation/configs/31-hybride.toml
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import random
import sys
from pathlib import Path
from typing import Protocol

from assistant import config
from assistant.evaluation import jeu as jeu_mod
from assistant.evaluation.reprise import ErreurTechnique, Reprise
from assistant.modeles import Morceau

SCHEMA_OR = {
    "type": "OBJECT",
    "properties": {
        "faits": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "fait": {"type": "STRING"},
                    "sections": {"type": "ARRAY", "items": {"type": "INTEGER"}},
                },
                "required": ["fait", "sections"],
            },
        }
    },
    "required": ["faits"],
}
CONTROLE_NOAN = 15


def corpus_numerote(morceaux: list[Morceau]) -> str:
    return "\n\n".join(f"[{i}] {m.contexte}\n{m.texte}" for i, m in enumerate(morceaux, 1))


def contenu_or(consigne: str, q: jeu_mod.QuestionEval, morceaux: list[Morceau]) -> str:
    return consigne.format(
        corpus=corpus_numerote(morceaux),
        question=q.question,
        faits="\n".join(f"- {f}" for f in q.faits_cles),
    )


class LecteurOr(Protocol):
    nom: str

    async def designer(self, contenu: str) -> dict: ...


class LecteurOrVertex:
    """Gemini, temperature 0, JSON sous schema. Non execute par les tests."""

    def __init__(self, projet: str, region: str, modele: str) -> None:
        from google import genai

        self._client = genai.Client(vertexai=True, project=projet, location=region)
        self.nom = modele

    async def designer(self, contenu: str) -> dict:
        from google.genai import types

        r = await self._client.aio.models.generate_content(
            model=self.nom,
            contents=contenu,
            config=types.GenerateContentConfig(
                temperature=0, response_mime_type="application/json", response_schema=SCHEMA_OR
            ),
        )
        return json.loads(r.text)


def _gold(reponse: dict, q: jeu_mod.QuestionEval, morceaux: list[Morceau]) -> list[dict]:
    faits = []
    for i, fait in enumerate(q.faits_cles):
        donne = (reponse.get("faits") or [])[i] if i < len(reponse.get("faits") or []) else {}
        sections = [
            n
            for n in (donne.get("sections") or [])
            if isinstance(n, int) and 1 <= n <= len(morceaux)
        ]
        faits.append(
            {
                "fait": fait,
                "passages": [
                    {
                        "id": morceaux[n - 1].id,
                        "url": morceaux[n - 1].url,
                        "ancre": morceaux[n - 1].ancre,
                    }
                    for n in dict.fromkeys(sections)
                ],
            }
        )
    return faits


async def passages_or(
    questions: list[jeu_mod.QuestionEval],
    morceaux: list[Morceau],
    lecteur: LecteurOr,
    consigne: str,
    parallele: int = 2,
    reprise: Reprise | None = None,
) -> dict:
    reprise = reprise or Reprise()
    sem = asyncio.Semaphore(parallele)
    par_langue = {
        lg: [m for m in morceaux if m.langue == lg] for lg in {m.langue for m in morceaux}
    }
    sortie: dict = {}

    async def une(q: jeu_mod.QuestionEval) -> None:
        corpus = par_langue.get(q.langue, [])
        async with sem:
            try:
                rep = await reprise.jouer(lambda: lecteur.designer(contenu_or(consigne, q, corpus)))
            except ErreurTechnique as e:
                sortie[q.id] = {"langue": q.langue, "erreur": str(e)[:300], "faits": []}
                return
        sortie[q.id] = {"langue": q.langue, "faits": _gold(rep, q, corpus)}

    await asyncio.gather(*(une(q) for q in questions if q.attendu == "repondre"))
    return dict(sorted(sortie.items()))


def a_controler(ids: list[str], n: int = CONTROLE_NOAN, graine: int = 20261124) -> list[str]:
    alea = random.Random(graine)  # noqa: S311 (tirage d'echantillon, pas de securite)
    return sorted(alea.sample(sorted(ids), min(n, len(ids))))


def touche(m: Morceau, g: dict) -> bool:
    """Un passage trouve porte un passage d'or : meme page et meme section, ou page entiere."""
    return m.url == g["url"] and (m.ancre == g["ancre"] or m.ancre == "")


def aire_roc(positifs: list[float], negatifs: list[float]) -> float | None:
    """P(distance d'une question a refuser > distance d'une question a repondre), egalites a 1/2."""
    if not positifs or not negatifs:
        return None
    total = 0.0
    for n in negatifs:
        for p in positifs:
            total += 1.0 if n > p else 0.5 if n == p else 0.0
    return round(total / (len(positifs) * len(negatifs)), 4)


def mesurer_rappel(
    questions: list[jeu_mod.QuestionEval],
    trouves: dict[str, list[Morceau]],
    distances: dict[str, float | None],
    gold: dict,
    k: int,
) -> dict:
    tout, un, rr, n = 0, 0, 0.0, 0
    manques = []
    for q in questions:
        faits = [f for f in (gold.get(q.id) or {}).get("faits", []) if f["passages"]]
        if q.attendu != "repondre" or not faits:
            continue
        n += 1
        top = trouves.get(q.id, [])[:k]
        couverts = [any(touche(m, g) for m in top for g in f["passages"]) for f in faits]
        tout += all(couverts)
        un += any(couverts)
        if not all(couverts):
            manques.append(q.id)
        rangs = [
            i
            for i, m in enumerate(top, 1)
            if any(touche(m, g) for f in faits for g in f["passages"])
        ]
        rr += 1 / rangs[0] if rangs else 0.0
    a_repondre = [
        distances[q.id]
        for q in questions
        if q.attendu == "repondre" and distances.get(q.id) is not None
    ]
    a_refuser = [
        distances[q.id]
        for q in questions
        if q.attendu == "refuser" and distances.get(q.id) is not None
    ]
    return {
        "k": k,
        "questions": n,
        "rappel_tous_les_faits": tout,
        "rappel_au_moins_un": un,
        "rang_moyen_reciproque": round(rr / n, 4) if n else None,
        "aire_roc_distance": aire_roc(a_repondre, a_refuser),
        "manques": manques,
    }


async def rechercher(r, morceaux: list[Morceau], questions: list[jeu_mod.QuestionEval], k: int):
    from assistant.fabrique import construire_recherche

    embedder, recherche = await construire_recherche(r, morceaux, memoire=True)
    trouves: dict[str, list[Morceau]] = {}
    distances: dict[str, float | None] = {}
    for q in questions:
        if q.attendu == "carte":
            continue
        v = (await embedder.vectoriser([q.question], "question"))[0]
        ps = await recherche.chercher(v, q.langue, k, texte=q.question)
        trouves[q.id] = [p.morceau for p in ps]
        distances[q.id] = min((p.distance for p in ps), default=None)
    return trouves, distances


def main_or(argv: list[str] | None = None) -> int:
    from assistant.corpus.servi import empreinte_corpus
    from assistant.fabrique import charger_corpus_brut

    p = argparse.ArgumentParser(description="Passages d'or : sections qui portent chaque fait cle.")
    p.add_argument("--fichier-jeu", type=Path, default=jeu_mod.JEU_TRAVAIL)
    p.add_argument("--config", type=Path, action="append", default=[])
    p.add_argument("--modele", default="gemini-3.8-flash")
    p.add_argument("--parallele", type=int, default=2)
    p.add_argument("--sortie", type=Path, default=Path("build/passages-or.json"))
    a = p.parse_args(argv)
    r = config.charger(superpositions=a.config)
    brut, _ = charger_corpus_brut(r)
    questions = jeu_mod.charger(a.fichier_jeu)
    consigne = jeu_mod.CONSIGNE_OR.read_text(encoding="utf-8")
    lecteur = LecteurOrVertex(r.moteur["projet_gcp"], r.moteur["region_redaction"], a.modele)
    gold = asyncio.run(passages_or(questions, brut, lecteur, consigne, a.parallele))
    sortie = {
        "le": dt.datetime.now(dt.UTC).isoformat(),
        "modele": a.modele,
        "consigne": hashlib.sha256(consigne.encode("utf-8")).hexdigest()[:12],
        "corpus_source": empreinte_corpus(brut),
        "jeu": jeu_mod.empreinte_fichier(a.fichier_jeu)[:12],
        "a_controler_par_noan": a_controler(list(gold)),
        "controle_noan": {},
        "questions": gold,
    }
    a.sortie.parent.mkdir(parents=True, exist_ok=True)
    a.sortie.write_text(json.dumps(sortie, ensure_ascii=False, indent=2), encoding="utf-8")
    vides = sum(1 for g in gold.values() if not any(f["passages"] for f in g["faits"]))
    print(f"{len(gold)} questions, {vides} sans aucun passage d'or -> {a.sortie}")
    print(f"A controler par Noan : {', '.join(sortie['a_controler_par_noan'])}")
    return 0


def main_rappel(argv: list[str] | None = None) -> int:
    from assistant.fabrique import charger_corpus

    p = argparse.ArgumentParser(description="Rappel hors ligne d'une variante de recherche.")
    p.add_argument("--or", dest="fichier_or", type=Path, required=True)
    p.add_argument("--fichier-jeu", type=Path, default=jeu_mod.JEU_TRAVAIL)
    p.add_argument("--config", type=Path, action="append", default=[])
    p.add_argument("--corpus", type=Path, help="autre corpus (variante de decoupage)")
    p.add_argument("--k", type=int, default=6)
    p.add_argument("--sortie", type=Path)
    a = p.parse_args(argv)
    env = None
    if a.corpus:
        import os

        env = {**os.environ, "ASSISTANT_CORPUS_FICHIER": str(a.corpus)}
    r = config.charger(superpositions=a.config, env=env)
    morceaux, _ = charger_corpus(r)
    questions = jeu_mod.charger(a.fichier_jeu)
    gold = json.loads(a.fichier_or.read_text(encoding="utf-8"))["questions"]
    trouves, distances = asyncio.run(rechercher(r, morceaux, questions, a.k))
    m = mesurer_rappel(questions, trouves, distances, gold, a.k)
    m["config"] = {"superpositions": [str(c) for c in a.config], "empreinte": r.empreinte}
    print(
        f"rappel@{a.k} : {m['rappel_tous_les_faits']} sur {m['questions']} (tous les faits), "
        f"{m['rappel_au_moins_un']} (au moins un) ; rang moyen reciproque "
        f"{m['rang_moyen_reciproque']} ; aire ROC de la distance {m['aire_roc_distance']}"
    )
    if a.sortie:
        a.sortie.parent.mkdir(parents=True, exist_ok=True)
        a.sortie.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main_rappel())
