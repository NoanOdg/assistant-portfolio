"""Joue le jeu d'evaluation, juge, mesure, ecrit le resultat.

En local (faux, hors ligne) :
    assistant-evaluer --cible local --juge heuristique --sortie build/eval-locale.json
Sur la revision candidate (CI) :
    assistant-evaluer --cible http --url "$URL_CANDIDATE" --juge vertex \
        --sortie build/eval-candidate.json
Une configuration d'experience, en processus, superposee aux reglages (repetable,
dans l'ordre : chaque brique gagnante devient la base de la suivante), rejouee
K fois (un fichier par passage : sortie-r1.json, sortie-r2.json...) :
    assistant-evaluer --cible local --config evaluation/configs/10-c0.toml \
        --repetitions 2 --parallele 2 --juge vertex --version-juge v2 \
        --fichier-jeu evaluation/jeu/questions.yaml --sortie build/comparaison/c0.json
Raccourcis gardes : --mode tout_le_corpus (C), --branche agent (A).
Reserve scellee, une seule fois :
    assistant-evaluer --jeu reserve --motif "avant publication des chiffres" ...

Chaque sortie enregistre l'empreinte de la configuration effective, de la
consigne, du corpus servi, du corpus lu, du jeu et du juge. Une erreur 429 ou
5xx est rejouee avec attente exponentielle ; une erreur qui persiste est notee
`erreur_technique`, rejouee en fin de passage, jamais comptee comme fausse.
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
from assistant.evaluation.juge import TRANSMIS_MAX, Juge, JugeHeuristique, JugeVertex
from assistant.evaluation.mesures import ERREUR_TECHNIQUE, calculer, texte_rapport
from assistant.evaluation.reprise import ErreurTechnique, Reprise
from assistant.filtres import citations_valides
from assistant.modeles import Evenement

JOURNAL_RESERVE = jeu_mod.DOSSIER / "resultats" / "journal-reserve.jsonl"
CLES_BILAN = (
    "chiffres_remplaces",
    "chiffres_non_attribues",
    "premier_mot_ms",
    "duree_ms",
    "cout_usd",
    "cout_production_usd",
    "meilleure_distance",
    "jetons_entree",
    "jetons_sortie",
    "jetons_cache",
    "jetons_reflexion",
    "appels_modele",
    "appels_outils",
    "aiguillage",
    "intention",
)


def projet_de_url(url: str) -> str:
    if urlparse(url).path.lower().endswith(".pdf"):
        return "cv"
    morceaux = [p for p in urlparse(url).path.strip("/").split("/") if p and p != "en"]
    if len(morceaux) >= 2 and morceaux[0] == "projets":
        return morceaux[1]
    return morceaux[0] if morceaux else "accueil"


class ErreurService(RuntimeError):
    """Le service a repondu `erreur` (exception cote serveur, Vertex en 429 par exemple)."""

    code = 503  # passagere : rejouee par la reprise


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
        "question": q.question,
        "langue": q.langue,
        "famille": q.famille,
        "groupe": q.groupe or q.id,
        "attendu": q.attendu,
        "texte": "",
        "sources": [],
        "citees": False,
        "carte": False,
        "projets_passages": [],
        "chemin": "inconnu",
    }
    cites: list[int] | None = None
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
            res["passages"] = [
                {
                    "n": p.get("n", i),
                    "id": p.get("id", ""),
                    "projet": p["projet"],
                    "url": p.get("url", ""),
                    "distance": p.get("distance"),
                    "texte": p.get("texte", ""),
                }
                for i, p in enumerate(d["passages"], 1)
            ]
            res["architecture"] = d.get("architecture", "")
            res["brut"] = d.get("brut", "")
            cites = d.get("cites")
            if cites is None:  # service plus ancien : on relit les renvois affiches
                cites = citations_valides(d.get("affiche", ""), len(d["passages"]))
        elif e.type == "fin":
            res["chemin"] = d["chemin"]
            b = d.get("bilan", {})
            for k in CLES_BILAN:
                res[k] = b.get(k)
    if res["chemin"] == "erreur":
        raise ErreurService("le service a repondu par une erreur")
    passages = res.get("passages") or []
    voulus = set(cites or [])
    res["passages_cites"] = [dict(p) for p in passages if p["n"] in voulus]
    if len(passages) > TRANSMIS_MAX:
        # Tout le corpus transmis (C) : le texte se relit dans le corpus par
        # identifiant, seuls les passages cites le gardent (sorties legeres).
        for p in passages:
            if p["n"] not in voulus:
                p.pop("texte", None)
    res["projets_cites"] = (
        sorted({projet_de_url(u) for u in res["sources"]}) if res["citees"] else []
    )
    return res


def resultat_erreur(q: jeu_mod.QuestionEval, message: str) -> dict:
    return {
        "id": q.id,
        "question": q.question,
        "langue": q.langue,
        "famille": q.famille,
        "groupe": q.groupe or q.id,
        "attendu": q.attendu,
        "texte": "",
        "sources": [],
        "citees": False,
        "carte": False,
        "projets_passages": [],
        "chemin": ERREUR_TECHNIQUE,
        "erreur": message[:500],
    }


async def evaluer(
    questions: list[jeu_mod.QuestionEval],
    cible: Cible,
    juge: Juge,
    parallele: int = 4,
    reprise: Reprise | None = None,
) -> tuple[dict[str, dict], dict]:
    reprise = reprise or Reprise()

    async def une(q: jeu_mod.QuestionEval, sem: asyncio.Semaphore) -> dict:
        async with sem:
            try:
                res = await reprise.jouer(lambda: resultat(cible, q))
            except ErreurTechnique as e:
                return resultat_erreur(q, str(e))
            try:
                jugement = await reprise.jouer(lambda: juge.juger(q, res))
            except ErreurTechnique as e:
                return resultat_erreur(q, f"juge : {e}")
            res["jugement"] = jugement.vers_dict()
            return res

    sem = asyncio.Semaphore(parallele)
    tous = await asyncio.gather(*(une(q, sem) for q in questions))
    resultats = {r["id"]: r for r in tous}
    # Erreurs techniques : rejouees une fois en fin de passage, une a la fois.
    seul = asyncio.Semaphore(1)
    for q in questions:
        if resultats[q.id]["chemin"] == ERREUR_TECHNIQUE:
            resultats[q.id] = await une(q, seul)
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


def env_raccourcis(mode: str | None, branche: str | None) -> dict[str, str]:
    """--mode et --branche, traduits en variables d'environnement de reglages."""
    env = dict(os.environ)
    if mode:
        env["ASSISTANT_RECHERCHE_MODE"] = mode
        if mode == "tout_le_corpus":
            env.setdefault("ASSISTANT_MOTEUR_ORDRE_CONTENU", "passages_d_abord")
    if branche:
        env["ASSISTANT_MOTEUR_BRANCHE"] = branche
    return env


def reglages(a: argparse.Namespace) -> config.Reglages:
    return config.charger(
        superpositions=a.config, env=env_raccourcis(a.mode, getattr(a, "branche", None))
    )


def chemin_repetition(sortie: Path, k: int, total: int) -> Path:
    return sortie if total == 1 else sortie.with_name(f"{sortie.stem}-r{k}{sortie.suffix}")


def construire_juge(a: argparse.Namespace, r: config.Reglages, corpus) -> Juge:
    if a.juge != "vertex":
        return JugeHeuristique()
    tarif = r.tarif(a.modele_juge) if a.modele_juge in r.modeles else None
    return JugeVertex(
        r.moteur["projet_gcp"],
        r.moteur["region_redaction"],
        a.modele_juge,
        version=a.version_juge,
        corpus=corpus,
        tarif=tarif,
    )


def questions_du_jeu(a: argparse.Namespace) -> tuple[list[jeu_mod.QuestionEval], Path]:
    if a.jeu == "reserve":
        _garde_reserve(a.motif, a.rejouer_reserve)
        return jeu_mod.charger(jeu_mod.RESERVE), jeu_mod.RESERVE
    if a.fichier_jeu:
        if a.fichier_jeu.resolve() == jeu_mod.RESERVE.resolve():
            raise SystemExit("la reserve scellee se joue par --jeu reserve, avec son motif")
        return jeu_mod.charger(a.fichier_jeu), a.fichier_jeu
    return jeu_mod.charger(jeu_mod.JEU_TRAVAIL), jeu_mod.JEU_TRAVAIL


async def principal(a: argparse.Namespace) -> int:
    from assistant.corpus.servi import empreinte_corpus
    from assistant.fabrique import charger_corpus_brut, construire

    questions, fichier_jeu = questions_du_jeu(a)
    r = reglages(a)
    brut: list = []
    empreintes = {"jeu": jeu_mod.empreinte_fichier(fichier_jeu)[:12]}
    if a.cible == "http":
        jeton = os.environ.get("ASSISTANT_JETON_EVALUATION", "")
        if not a.url or not jeton:
            raise SystemExit("--url et ASSISTANT_JETON_EVALUATION sont requis pour la cible http")
        cible: Cible = CibleHttp(a.url, jeton)
        if a.juge == "vertex" and a.version_juge == "v2":
            brut, _ = charger_corpus_brut(r)
        cfg = {"service": a.url}
        if brut:
            empreintes["corpus_source"] = empreinte_corpus(brut)
    else:
        brut, _ = charger_corpus_brut(r)
        c = await construire(r)
        cible = CibleLocale(c.assistant)
        cfg = {
            "superpositions": [str(p) for p in a.config],
            "empreinte": r.empreinte,
            "effective": config.effective(r.donnees()),
        }
        empreintes.update(
            config=r.empreinte,
            consigne=c.assistant.version_consigne,
            corpus=c.empreinte_corpus,
            corpus_source=c.empreinte_corpus_source,
        )
    juge = construire_juge(a, r, brut)
    empreintes["juge"] = f"{juge.nom}/{juge.version}/{juge.empreinte}"
    restantes = 0
    for k in range(1, a.repetitions + 1):
        resultats, mesures = await evaluer(questions, cible, juge, a.parallele)
        restantes += len(mesures["erreurs_techniques"])
        sortie = {
            "le": dt.datetime.now(dt.UTC).isoformat(),
            "jeu": str(a.fichier_jeu) if a.fichier_jeu else a.jeu,
            "fichier_jeu": str(fichier_jeu),
            "cible": a.cible,
            "mode": r.mode_recherche if a.cible == "local" else "service",
            "branche": r.branche if a.cible == "local" else "service",
            "architecture": r.architecture if a.cible == "local" else "service",
            "juge": juge.nom,
            "juge_version": juge.version,
            "config": cfg,
            "empreintes": empreintes,
            "repetition": k,
            "repetitions": a.repetitions,
            "parallele": a.parallele,
            "mesures": mesures,
            "resultats": resultats,
        }
        chemin = chemin_repetition(a.sortie, k, a.repetitions)
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(json.dumps(sortie, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n=== Passage {k} sur {a.repetitions} ===")
        print(texte_rapport(mesures))
        print(f"\n-> {chemin}")
    if restantes:
        print(f"\nATTENTION : {restantes} erreur(s) technique(s) restante(s) : passage a rejouer")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Joue le jeu d'evaluation.")
    p.add_argument("--jeu", choices=["travail", "reserve"], default="travail")
    p.add_argument(
        "--fichier-jeu",
        type=Path,
        help="autre jeu a jouer a la place du jeu de travail (brouillon v2, par exemple)",
    )
    p.add_argument("--cible", choices=["local", "http"], default="local")
    p.add_argument("--url")
    p.add_argument(
        "--config",
        type=Path,
        action="append",
        default=[],
        help="fichier superpose aux reglages (evaluation/configs/NN-nom.toml), repetable",
    )
    p.add_argument("--repetitions", type=int, default=1, help="passages de la meme configuration")
    p.add_argument("--parallele", type=int, default=4, help="questions jouees en meme temps")
    p.add_argument("--mode", choices=["rag", "tout_le_corpus"], help="raccourci de recherche.mode")
    p.add_argument(
        "--branche",
        choices=["chemin", "agent"],
        help="branche jouee en processus (defaut : moteur.branche des reglages)",
    )
    p.add_argument("--juge", choices=["heuristique", "vertex"], default="heuristique")
    p.add_argument("--modele-juge", default="gemini-3.8-flash")
    p.add_argument(
        "--version-juge",
        choices=["v1", "v2"],
        default="v1",
        help="v2 : reference fixe, la meme pour A, B et C (etude, fiche 3.11)",
    )
    p.add_argument("--sortie", type=Path, default=Path("build/evaluation.json"))
    p.add_argument("--motif")
    p.add_argument("--rejouer-reserve", action="store_true")
    a = p.parse_args(argv)
    if a.fichier_jeu and a.jeu == "reserve":
        p.error("--fichier-jeu et --jeu reserve s'excluent")
    if a.cible != "local" and (a.mode or a.branche or a.config):
        p.error("--mode, --branche et --config se jouent en processus (--cible local)")
    if a.mode == "tout_le_corpus" and a.branche == "agent":
        p.error("tout_le_corpus est une variante du chemin fixe : --branche chemin")
    if a.repetitions < 1 or a.parallele < 1:
        p.error("--repetitions et --parallele valent au moins 1")
    if a.jeu == "reserve" and a.repetitions != 1:
        p.error("la reserve se joue une seule fois")
    return asyncio.run(principal(a))


if __name__ == "__main__":
    sys.exit(main())
