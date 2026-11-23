"""Assemble les composants selon le mode : local (faux, memoire) ou vertex (Google Cloud)."""

from __future__ import annotations

import datetime as dt
import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from assistant.agent import AssistantAgent, Bornes, FauxModeleAgent, ModeleAgent, VertexModeleAgent
from assistant.config import RACINE, Reglages
from assistant.corpus.ingestion import lire
from assistant.embeddings import FauxEmbedder, VertexEmbedder
from assistant.generation import FauxGenerateur, VertexGenerateur
from assistant.limites import (
    CompteurFirestore,
    CompteurMemoire,
    CompteurQuotidien,
    LimiteurVisiteur,
)
from assistant.modeles import Morceau
from assistant.pipeline import Assistant, Parametres
from assistant.recherche import RechercheFirestore, RechercheMemoire
from assistant.session import (
    AntiRobot,
    AntiRobotDesactive,
    IdentiteEvaluation,
    IdentiteGoogle,
    IdentiteRefusee,
    Sessions,
    Turnstile,
)


class JournalQuestions(Protocol):
    async def garder(self, question: str, langue: str, chemin: str) -> None: ...


class JournalMuet:
    async def garder(self, question: str, langue: str, chemin: str) -> None:
        return None


class JournalFirestore:
    """questions/{auto} avec `expire_le` : la politique TTL de Firestore purge apres 30 jours."""

    def __init__(self, projet: str, jours: int) -> None:
        from google.cloud import firestore

        self._db = firestore.AsyncClient(project=projet)
        self._jours = jours

    async def garder(self, question: str, langue: str, chemin: str) -> None:
        maintenant = dt.datetime.now(dt.UTC)
        await self._db.collection("questions").add(
            {
                "question": question,
                "langue": langue,
                "chemin": chemin,
                "le": maintenant,
                "expire_le": maintenant + dt.timedelta(days=self._jours),
            }
        )


@dataclass
class Composants:
    reglages: Reglages
    assistant: Assistant
    limiteur: LimiteurVisiteur
    compteur: CompteurQuotidien
    sessions: Sessions
    anti_robot: AntiRobot
    identite_eval: IdentiteEvaluation
    journal_questions: JournalQuestions


def chemin_corpus(r: Reglages) -> Path:
    p = Path(r.corpus["fichier"])
    return p if p.is_absolute() else RACINE / p


def charger_corpus(r: Reglages) -> tuple[list[Morceau], dict]:
    fichier = chemin_corpus(r)
    if not fichier.exists():
        raise FileNotFoundError(
            f"{fichier} absent : lancer d'abord assistant-ingerer (voir README)"
        )
    coord = fichier.parent / "coordonnees.json"
    return lire(fichier), (json.loads(coord.read_text(encoding="utf-8")) if coord.exists() else {})


def parametres(r: Reglages) -> Parametres:
    return Parametres(
        seuil_distance=r.seuil_distance,
        passages=int(r.recherche["passages"]),
        jetons_reponse=int(r.limites["jetons_reponse"]),
        delai_s=float(r.limites["delai_reponse_s"]),
        domaines=tuple(r.securite["domaines_autorises"]),
        site=r.site["url"],
        tarif_entree=float(r.tarifs["redaction_entree"]),
        tarif_sortie=float(r.tarifs["redaction_sortie"]),
        versions={
            "modele": r.moteur["modele_redaction"],
            "corpus": r.corpus["version"],
            "branche": r.branche,
        },
    )


def bornes(r: Reglages) -> Bornes:
    a, d = r.agent, Bornes()
    return Bornes(
        max_appels_outils=int(a.get("max_appels_outils", d.max_appels_outils)),
        max_tours=int(a.get("max_tours", d.max_tours)),
        budget_jetons=int(a.get("budget_jetons", d.budget_jetons)),
        caracteres_page=int(a.get("caracteres_page", d.caracteres_page)),
    )


def construire_modele_agent(r: Reglages) -> ModeleAgent:
    """Branche A : meme modele, meme region, memes reglages que le redacteur de B."""
    if r.mode == "local":
        return FauxModeleAgent()
    m = r.moteur
    return VertexModeleAgent(
        m["projet_gcp"],
        m["region_redaction"],
        m["modele_redaction"],
        float(m["temperature"]),
        str(m["reflexion"]),
    )


async def construire_recherche(r: Reglages, morceaux: list[Morceau]):
    """Embedder et recherche seuls, sans la verification de demarrage : sert au
    calage du seuil, qui doit tourner justement quand le seuil n'est pas cale."""
    m = r.moteur
    if r.mode == "local":
        embedder = FauxEmbedder(int(m["dimensions"]))
        return embedder, await RechercheMemoire.construire(morceaux, embedder)
    embedder = VertexEmbedder(
        m["projet_gcp"], m["region"], m["modele_embeddings"], int(m["dimensions"])
    )
    return embedder, RechercheFirestore(
        m["projet_gcp"], r.corpus["collection"], r.corpus["version"]
    )


async def construire(
    r: Reglages, morceaux: list[Morceau] | None = None, coordonnees: dict | None = None
) -> Composants:
    r.verifier()
    if morceaux is None or coordonnees is None:
        lus, coord = charger_corpus(r)
        morceaux = lus if morceaux is None else morceaux
        coordonnees = coord if coordonnees is None else coordonnees
    lim = r.limites
    m = r.moteur
    if r.mode == "local":
        embedder, recherche = await construire_recherche(r, morceaux)
        generateur = FauxGenerateur()
        compteur: CompteurQuotidien = CompteurMemoire()
        cle = r.secrets.get("session", "").encode() or secrets.token_bytes(32)
        journal_q: JournalQuestions = JournalMuet()
    else:
        embedder, recherche = await construire_recherche(r, morceaux)
        generateur = VertexGenerateur(
            m["projet_gcp"],
            m["region_redaction"],
            m["modele_redaction"],
            float(m["temperature"]),
            str(m["reflexion"]),
        )
        compteur = CompteurFirestore(m["projet_gcp"])
        cle = r.secrets["session"].encode()
        journal_q = (
            JournalFirestore(m["projet_gcp"], int(r.journal["duree_questions_jours"]))
            if r.journal["garder_questions"]
            else JournalMuet()
        )
    anti_robot: AntiRobot = (
        Turnstile(r.secrets["turnstile"])
        if r.securite["anti_robot"] == "turnstile"
        else AntiRobotDesactive()
    )
    identite: IdentiteEvaluation = (
        IdentiteGoogle(r.securite["audience_evaluation"], r.securite["compte_evaluation"])
        if r.securite["compte_evaluation"]
        else IdentiteRefusee()
    )
    assistant: Assistant
    if r.branche == "agent":
        assistant = AssistantAgent(
            embedder,
            recherche,
            construire_modele_agent(r),
            parametres(r),
            coordonnees,
            morceaux,
            bornes(r),
        )
    else:
        assistant = Assistant(embedder, recherche, generateur, parametres(r), coordonnees)
    return Composants(
        reglages=r,
        assistant=assistant,
        limiteur=LimiteurVisiteur(
            int(lim["questions_par_minute"]), int(lim["questions_par_jour_visiteur"])
        ),
        compteur=compteur,
        sessions=Sessions(cle, int(lim["duree_session_s"])),
        anti_robot=anti_robot,
        identite_eval=identite,
        journal_questions=journal_q,
    )
