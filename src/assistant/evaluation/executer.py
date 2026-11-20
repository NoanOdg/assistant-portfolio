"""Joue le jeu d'evaluation, juge, mesure, ecrit le resultat.

En local (faux, hors ligne) :
    assistant-evaluer --cible local --juge heuristique --sortie build/eval-locale.json
Sur la revision candidate (CI) :
    assistant-evaluer --cible http --url "$URL_CANDIDATE" --juge vertex \
        --sortie build/eval-candidate.json
Comparaison C, tout le site de la langue dans la consigne :
    assistant-evaluer --cible local --mode tout_le_corpus ...
Reserve scellee, une seule fois :
    assistant-evaluer --jeu reserve --motif "avant publication des chiffres" ...
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

import httpx

from assistant import config
from assistant.evaluation import jeu as jeu_mod
from assistant.evaluation.juge import Juge, JugeHeuristique, JugeVertex
from assistant.evaluation.mesures import calculer, texte_rapport
from assistant.modeles import Evenement

JOURNAL_RESERVE = jeu_mod.DOSSIER / "resultats" / "journal-reserve.jsonl"


def projet_de_url(url: str) -> str:
    morceaux = [p for p in urlparse(url).path.strip("/").split("/") if p and p != "en"]
    if len(morceaux) >= 2 and morceaux[0] == "projets":
        return morceaux[1]
    return morceaux[0] if morceaux else "accueil"


class Cible(Protocol):
    def poser(self, question: str, langue: str) -> AsyncIterator[Evenement]: ...


class CibleLocale:
    def __init__(self, assistant) -> None:
        self.assistant = assistant

    async def poser(self, question: str, langue: str) -> AsyncIterator[Evenement]:
        async for e in self.assistant.repondre(question, langue, diagnostic=True):
            yield e


class CibleHttp:
    """Lit le flux SSE de /question avec le jeton d'identite de l'evaluation."""

    def __init__(self, url: str, jeton: str, client: httpx.AsyncClient | None = None) -> None:
        self.url = url.rstrip("/")
        self.jeton = jeton
        self.client = client or httpx.AsyncClient(timeout=60)

    async def poser(self, question: str, langue: str) -> AsyncIterator[Evenement]:
        async with self.client.stream(
            "POST",
            f"{self.url}/question",
            json={"question": question, "langue": langue},
            headers={"X-Jeton-Evaluation": self.jeton},
        ) as r:
            r.raise_for_status()
            type_, donnees = "", []
            async for ligne in r.aiter_lines():
                if ligne.startswith("event:"):
                    type_ = ligne[6:].strip()
                elif ligne.startswith("data:"):
                    donnees.append(ligne[5:].strip())
                elif ligne == "" and type_:
                    yield Evenement(type_, json.loads("".join(donnees) or "{}"))
                    type_, donnees = "", []


async def resultat(cible: Cible, q: jeu_mod.QuestionEval) -> dict:
    res: dict = {
        "id": q.id,
        "texte": "",
        "sources": [],
        "citees": False,
        "carte": False,
        "projets_passages": [],
        "chemin": "inconnu",
    }
    async for e in cible.poser(q.question, q.langue):
        d = e.donnees
        if e.type == "texte" or e.type == "refus":
            res["texte"] += d["texte"]
        elif e.type == "carte":
            res["carte"] = True
        elif e.type == "sources":
            res["sources"] = [s["url"] for s in d["sources"]]
            res["citees"] = d["citees"]
        elif e.type == "diagnostic":
            res["projets_passages"] = sorted({p["projet"] for p in d["passages"]})
            res["brut"] = d.get("brut", "")
        elif e.type == "fin":
            res["chemin"] = d["chemin"]
            b = d.get("bilan", {})
            for k in (
                "chiffres_remplaces",
                "premier_mot_ms",
                "duree_ms",
                "cout_usd",
                "meilleure_distance",
                "jetons_entree",
                "jetons_sortie",
            ):
                res[k] = b.get(k)
    res["projets_cites"] = (
        sorted({projet_de_url(u) for u in res["sources"]}) if res["citees"] else []
    )
    return res


async def evaluer(
    questions: list[jeu_mod.QuestionEval], cible: Cible, juge: Juge, parallele: int = 4
) -> tuple[dict[str, dict], dict]:
    sem = asyncio.Semaphore(parallele)

    async def une(q: jeu_mod.QuestionEval) -> dict:
        async with sem:
            res = await resultat(cible, q)
            res["jugement"] = (await juge.juger(q, res)).vers_dict()
            return res

    tous = await asyncio.gather(*(une(q) for q in questions))
    resultats = {r["id"]: r for r in tous}
    return resultats, calculer(questions, resultats)


def _garde_reserve(motif: str | None, rejouer: bool) -> str:
    empreinte = jeu_mod.verifier_reserve()
    if not motif:
        raise SystemExit("la reserve scellee exige --motif (pourquoi la jouer maintenant)")
    deja = []
    if JOURNAL_RESERVE.exists():
        deja = [
            json.loads(x)
            for x in JOURNAL_RESERVE.read_text(encoding="utf-8").splitlines()
            if x.strip()
        ]
    if any(d["empreinte"] == empreinte for d in deja) and not rejouer:
        raise SystemExit(
            "la reserve a deja ete jouee : --rejouer-reserve pour passer outre, "
            "et le dire dans la page projet"
        )
    JOURNAL_RESERVE.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL_RESERVE.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {"le": dt.datetime.now(dt.UTC).isoformat(), "motif": motif, "empreinte": empreinte},
                ensure_ascii=False,
            )
            + "\n"
        )
    return empreinte


async def _cible_locale(mode: str):
    from assistant.fabrique import charger_corpus, construire
    from assistant.recherche import RechercheToutLeCorpus

    r = config.charger()
    morceaux, coord = charger_corpus(r)
    c = await construire(r, morceaux, coord)
    if mode == "tout_le_corpus":
        c.assistant.recherche = RechercheToutLeCorpus(morceaux)
        c.assistant.p.appliquer_seuil = False
        c.assistant.p.passages = 10_000
    return CibleLocale(c.assistant)


async def principal(a: argparse.Namespace) -> int:
    if a.jeu == "reserve":
        _garde_reserve(a.motif, a.rejouer_reserve)
        questions = jeu_mod.charger(jeu_mod.RESERVE)
    else:
        questions = jeu_mod.charger(jeu_mod.JEU_TRAVAIL)
    if a.cible == "http":
        jeton = os.environ.get("ASSISTANT_JETON_EVALUATION", "")
        if not a.url or not jeton:
            raise SystemExit("--url et ASSISTANT_JETON_EVALUATION sont requis pour la cible http")
        cible: Cible = CibleHttp(a.url, jeton)
    else:
        cible = await _cible_locale(a.mode)
    if a.juge == "vertex":
        r = config.charger()
        juge: Juge = JugeVertex(r.moteur["projet_gcp"], r.moteur["region"], a.modele_juge)
    else:
        juge = JugeHeuristique()
    resultats, mesures = await evaluer(questions, cible, juge)
    sortie = {
        "le": dt.datetime.now(dt.UTC).isoformat(),
        "jeu": a.jeu,
        "cible": a.cible,
        "mode": a.mode,
        "juge": juge.nom,
        "mesures": mesures,
        "resultats": resultats,
    }
    a.sortie.parent.mkdir(parents=True, exist_ok=True)
    a.sortie.write_text(json.dumps(sortie, ensure_ascii=False, indent=2), encoding="utf-8")
    print(texte_rapport(mesures))
    print(f"\n-> {a.sortie}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Joue le jeu d'evaluation.")
    p.add_argument("--jeu", choices=["travail", "reserve"], default="travail")
    p.add_argument("--cible", choices=["local", "http"], default="local")
    p.add_argument("--url")
    p.add_argument("--mode", choices=["rag", "tout_le_corpus"], default="rag")
    p.add_argument("--juge", choices=["heuristique", "vertex"], default="heuristique")
    p.add_argument("--modele-juge", default="gemini-3.8-flash")
    p.add_argument("--sortie", type=Path, default=Path("build/evaluation.json"))
    p.add_argument("--motif")
    p.add_argument("--rejouer-reserve", action="store_true")
    a = p.parse_args(argv)
    if a.mode == "tout_le_corpus" and a.cible != "local":
        p.error("la comparaison tout_le_corpus se joue en processus (--cible local)")
    return asyncio.run(principal(a))


if __name__ == "__main__":
    sys.exit(main())
