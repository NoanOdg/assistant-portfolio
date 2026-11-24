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
from assistant.corpus.servi import empreinte_corpus, selon_reglages
from assistant.embeddings import Embedder, FauxEmbedder, VertexEmbedder
from assistant.generation import FauxGenerateur, VertexGenerateur
from assistant.limites import (
    CompteurFirestore,
    CompteurMemoire,
    CompteurQuotidien,
    LimiteurVisiteur,
)
from assistant.modeles import Morceau
from assistant.pipeline import Assistant, Parametres, charger_consigne
from assistant.recherche import (
    Recherche,
    RechercheFirestore,
    RechercheHybride,
    RechercheMemoire,
    RechercheParente,
)
from assistant.session import (
    Altcha,
    AntiRobot,
    AntiRobotDesactive,
    IdentiteEvaluation,
    IdentiteGoogle,
    IdentiteRefusee,
    Sessions,
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
    # Empreintes du corpus lu (fichier entier) et du corpus servi (apres reglages).
    empreinte_corpus_source: str = ""
    empreinte_corpus: str = ""


def chemin_corpus(r: Reglages) -> Path:
    p = Path(r.corpus["fichier"])
    return p if p.is_absolute() else RACINE / p


def charger_corpus_brut(r: Reglages) -> tuple[list[Morceau], dict]:
    """Tout le fichier, page Contact comprise : pour les empreintes et le juge."""
    fichier = chemin_corpus(r)
    if not fichier.exists():
        raise FileNotFoundError(
            f"{fichier} absent : lancer d'abord assistant-ingerer (voir README)"
        )
    coord = fichier.parent / "coordonnees.json"
    return lire(fichier), (json.loads(coord.read_text(encoding="utf-8")) if coord.exists() else {})


def charger_corpus(r: Reglages) -> tuple[list[Morceau], dict]:
    """Les morceaux servis selon `[corpus]` (inclure_contact, compact), et la carte."""
    morceaux, coord = charger_corpus_brut(r)
    return selon_reglages(morceaux, r.corpus), coord


def parametres(r: Reglages) -> Parametres:
    m = r.moteur
    tarif = r.tarif(m["modele_redaction"])
    return Parametres(
        seuil_distance=r.seuil_distance,
        passages=int(r.recherche["passages"]),
        jetons_reponse=int(r.limites["jetons_reponse"]),
        delai_s=float(r.limites["delai_reponse_s"]),
        delai_premier_s=float(r.limites.get("delai_premier_mot_s", 8)),
        relances_premier=int(r.limites.get("relances_premier_mot", 1)),
        domaines=tuple(r.securite["domaines_autorises"]),
        site=r.site["url"],
        tarif_entree=tarif["entree"],
        tarif_sortie=tarif["sortie"],
        tarif_cache=tarif["cache"],
        mode_recherche=r.mode_recherche,
        prefiltre_seuil=bool(r.refus.get("prefiltre_seuil", False)),
        ordre_contenu=str(m.get("ordre_contenu", "question_d_abord")),
        date_du_jour=bool(m.get("date_du_jour", False)),
        aiguillage=str(r.aiguillage.get("version", "v1")),
        etiquette_modele=bool(r.aiguillage.get("etiquette_modele", False)),
        versions={
            "modele": m["modele_redaction"],
            "corpus": r.corpus["version"],
            "branche": r.branche,
            "architecture": r.architecture,
            "config": r.empreinte,
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


def construire_anti_robot(r: Reglages) -> AntiRobot:
    """ALTCHA, cle de Secret Manager. En local sans cle : une cle tiree au demarrage."""
    if r.securite["anti_robot"] != "altcha":
        return AntiRobotDesactive()
    a = r.altcha
    return Altcha(
        r.secrets.get("altcha") or secrets.token_hex(32),
        algorithme=str(a.get("algorithme", "PBKDF2/SHA-256")),
        cout=int(a.get("cout", 5000)),
        prefixe=str(a.get("prefixe", "00")),
        expiration_s=int(a.get("expiration_s", 120)),
    )


def construire_embedder(r: Reglages) -> Embedder:
    m = r.moteur
    if r.mode == "local":
        return FauxEmbedder(int(m["dimensions"]))
    return VertexEmbedder(
        m["projet_gcp"],
        m["region"],
        m["modele_embeddings"],
        int(m["dimensions"]),
        tache_question=str(m.get("tache_question", "RETRIEVAL_QUERY")),
        titre_document=bool(m.get("titre_document", False)),
    )


def envelopper(r: Reglages, base: Recherche, morceaux: list[Morceau]) -> Recherche:
    """Variantes de recherche choisies dans les reglages (hybride, page parente)."""
    rech = r.recherche
    recherche = base
    if rech.get("hybride"):
        recherche = RechercheHybride(
            recherche,
            morceaux,
            int(rech.get("k_rrf", 60)),
            int(rech.get("candidats_hybride", 20)),
        )
    if r.corpus.get("decoupage") == "petit_grand":
        recherche = RechercheParente(recherche, morceaux, int(rech.get("candidats_hybride", 20)))
    return recherche


async def construire_recherche(r: Reglages, morceaux: list[Morceau], memoire: bool = False):
    """Embedder et recherche seuls, sans la verification de demarrage : sert au
    calage du seuil, qui doit tourner justement quand le seuil n'est pas cale.
    `memoire` : index en memoire meme sur Vertex (essais hors ligne de variantes
    d'embedding ou de decoupage, sans version Firestore par variante)."""
    embedder = construire_embedder(r)
    if r.mode == "local" or memoire:
        base: Recherche = await RechercheMemoire.construire(morceaux, embedder)
    else:
        base = RechercheFirestore(
            r.moteur["projet_gcp"], r.corpus["collection"], r.corpus["version"]
        )
    return embedder, envelopper(r, base, morceaux)


async def construire(
    r: Reglages, morceaux: list[Morceau] | None = None, coordonnees: dict | None = None
) -> Composants:
    r.verifier()
    source = ""
    if morceaux is None or coordonnees is None:
        lus, coord = charger_corpus_brut(r)
        source = empreinte_corpus(lus)
        morceaux = selon_reglages(lus, r.corpus) if morceaux is None else morceaux
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
    anti_robot = construire_anti_robot(r)
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
        assistant = Assistant(
            embedder,
            recherche,
            generateur,
            parametres(r),
            coordonnees,
            charger_consigne(
                str(m.get("consigne", "v1")), bool(r.aiguillage.get("etiquette_modele"))
            ),
            corpus=morceaux if r.architecture == "C" else None,
        )
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
        empreinte_corpus_source=source or empreinte_corpus(morceaux),
        empreinte_corpus=empreinte_corpus(morceaux),
    )
