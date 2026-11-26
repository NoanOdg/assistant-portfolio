"""API HTTP : FastAPI, reponses en flux SSE.

    GET  /defi      defi ALTCHA signe (preuve de travail, expire vite)
    POST /session   solution ALTCHA (+ ancien jeton, facultatif) -> jeton de
                    session signe (30 min), conversation reprise ou neuve
    POST /question  question + langue de la page -> flux d'evenements SSE
    GET  /sante     sonde de vie et de disponibilite

Contrat complet pour le widget du site : docs/api-widget.md.

Evenements du flux : conversation (memoire), etape, texte, sources, carte, refus, erreur, fin
(et diagnostic, pour l'evaluation seulement). En public, `fin` ne porte que
l'identifiant de trace : le bilan (cout, jetons, distance du meilleur passage,
chemin) aiderait a sonder le seuil de refus. Il reste dans le journal, et dans
le flux de l'evaluation (jeton d'identite Google du compte de deploiement).

Memoire de conversation (memoire.py) : la cle est l'identifiant de
conversation du jeton. L'evaluation, qui n'a pas de jeton de session, nomme sa
conversation par l'en-tete X-Conversation, lu seulement avec X-Jeton-Evaluation.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from assistant import config, journal, textes
from assistant.fabrique import Composants, construire
from assistant.modeles import Evenement
from assistant.session import JetonInvalide

VERSION = "0.1.0"


class DemandeSession(BaseModel):
    # Charge ALTCHA : base64 du JSON {"challenge": ..., "solution": ...}.
    altcha: str = Field(min_length=1, max_length=4096)
    # Jeton precedent, au renouvellement : sa conversation est reprise s'il est
    # signe, expire depuis moins de 30 min, et si elle a servi depuis moins de 30 min.
    precedente: str = Field(default="", max_length=1024)


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    langue: Literal["fr", "en"] = "fr"


# Identifiant de conversation de l'evaluation (en-tete X-Conversation).
CONVERSATION_EVAL = re.compile(r"[A-Za-z0-9_-]{1,64}")


def fin_publique(e: Evenement, trace: str, evaluation: bool) -> Evenement:
    """L'evaluation recoit chemin et bilan ; le visiteur, la trace seule."""
    if evaluation:
        return Evenement("fin", {**e.donnees, "trace": trace})
    return Evenement("fin", {"trace": trace})


def sse(e: Evenement) -> str:
    return f"event: {e.type}\ndata: {json.dumps(e.donnees, ensure_ascii=False)}\n\n"


def motif_origines(projet_pages: str) -> str | None:
    """Apercus Cloudflare Pages du projet : https://<empreinte ou branche>.<projet>.pages.dev,
    et l'alias de production https://<projet>.pages.dev. Vide : aucun apercu."""
    projet = projet_pages.strip().lower()
    if not projet:
        return None
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", projet):
        raise ValueError(f"nom de projet Cloudflare Pages invalide : {projet_pages!r}")
    return rf"https://(?:[a-z0-9-]+\.)?{projet}\.pages\.dev"


def adresse_client(request: Request, rang: int) -> str:
    """Sert seulement a la cle du limiteur (HMAC sale du jour). Jamais journalisee."""
    xff = request.headers.get("x-forwarded-for", "")
    morceaux = [m.strip() for m in xff.split(",") if m.strip()]
    if morceaux and rang >= 1:
        return morceaux[-rang] if len(morceaux) >= rang else morceaux[0]
    return request.client.host if request.client else "inconnu"


def creer_app(composants: Composants | None = None) -> FastAPI:
    journal.configurer()
    reglages = composants.reglages if composants else config.charger()

    @asynccontextmanager
    async def cycle(app: FastAPI):
        app.state.c = composants or await construire(reglages)
        journal.ecrire(
            "demarrage",
            version=VERSION,
            modele=reglages.moteur["modele_redaction"],
            corpus=reglages.corpus["version"],
            consigne=app.state.c.assistant.version_consigne,
            branche=app.state.c.assistant.branche,
        )
        yield

    app = FastAPI(
        title="Assistant du portfolio",
        version=VERSION,
        lifespan=cycle,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    sec = reglages.securite
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[*sec["origines"], *sec.get("origines_locales", [])],
        allow_origin_regex=motif_origines(str(sec.get("projet_pages", ""))),
        allow_methods=["POST", "GET"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )

    @app.get("/sante")
    async def sante() -> dict:
        return {"etat": "ok", "version": VERSION}

    @app.get("/defi")
    async def defi(request: Request) -> JSONResponse:
        c: Composants = request.app.state.c
        return JSONResponse(c.anti_robot.defi(), headers={"Cache-Control": "no-store"})

    @app.post("/session")
    async def session(demande: DemandeSession, request: Request):
        c: Composants = request.app.state.c
        motif = await c.anti_robot.verifier(demande.altcha)
        if motif:
            journal.ecrire("anti_robot", motif=motif, statut=403)
            return JSONResponse(
                {"detail": "verification anti-robots refusee", "motif": motif},
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        reprise = None
        if demande.precedente and c.memoire is not None:
            ancienne = c.sessions.conversation_precedente(demande.precedente)
            if ancienne and c.memoire.active(ancienne):
                reprise = ancienne
        return JSONResponse(
            {
                "session": c.sessions.emettre(conversation=reprise),
                "expire_dans": c.sessions.duree_s,
                "reprise": reprise is not None,
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/question")
    async def question(
        q: Question,
        request: Request,
        authorization: str = Header(default=""),
        x_jeton_evaluation: str = Header(default=""),
        x_conversation: str = Header(default=""),
        x_cloud_trace_context: str = Header(default=""),
    ):
        c: Composants = request.app.state.c
        r = c.reglages
        evaluation = bool(x_jeton_evaluation) and c.identite_eval.verifier(x_jeton_evaluation)
        cle_conversation = ""
        if not evaluation:
            jeton = authorization.removeprefix("Bearer ").strip()
            try:
                cle_conversation = c.sessions.verifier(jeton).conversation
            except (JetonInvalide, ValueError):
                raise HTTPException(status_code=401, detail="session absente ou expiree") from None
            motif = c.limiteur.autoriser(
                adresse_client(request, int(r.securite["rang_ip_depuis_la_fin"]))
            )
            if motif:
                journal.ecrire("limite", motif=motif, statut=429)
                return JSONResponse(
                    {"texte": textes.texte(q.langue, "plafond_visiteur")}, status_code=429
                )
            if not await c.compteur.prendre("global", int(r.limites["questions_par_jour_global"])):
                journal.ecrire("plafond", motif="global", statut=429)
                return JSONResponse(
                    {"texte": textes.texte(q.langue, "plafond_global")}, status_code=429
                )
        elif not await c.compteur.prendre(
            "evaluation", int(r.limites["questions_par_jour_evaluation"])
        ):
            return JSONResponse({"texte": "plafond d'evaluation atteint"}, status_code=429)
        if evaluation and CONVERSATION_EVAL.fullmatch(x_conversation):
            cle_conversation = "eval-" + x_conversation
        # La branche agent n'a pas de memoire : rien ne lui est passe.
        memoire = (
            {"conversation": c.memoire.ouvrir(cle_conversation)}
            if c.memoire is not None and cle_conversation
            else {}
        )
        # Conversation sans aucun echange : neuve (premiere question, ou memoire
        # perdue : instance arretee faute de trafic, redemarrage). Le widget
        # affiche alors son separateur s'il avait deja un fil.
        neuve = bool(memoire) and not memoire["conversation"].echanges

        trace = uuid.uuid4().hex
        trace_gcp = ""
        if x_cloud_trace_context and r.moteur["projet_gcp"]:
            trace_gcp = (
                f"projects/{r.moteur['projet_gcp']}/traces/{x_cloud_trace_context.split('/')[0]}"
            )

        async def flux() -> AsyncIterator[str]:
            chemin = "erreur"
            if neuve:
                yield sse(Evenement("conversation", {"neuve": True}))
            try:
                async for e in c.assistant.repondre(
                    q.question, q.langue, diagnostic=evaluation, **memoire
                ):
                    if e.type == "fin":
                        chemin = e.donnees["chemin"]
                        journal.ecrire(
                            "question",
                            trace=trace,
                            trace_gcp=trace_gcp,
                            langue=q.langue,
                            evaluation=evaluation,
                            modele=c.assistant.generateur.nom,
                            corpus=r.corpus["version"],
                            consigne=c.assistant.version_consigne,
                            branche=c.assistant.branche,
                            **e.donnees["bilan"],
                        )
                        e = fin_publique(e, trace, evaluation)
                    yield sse(e)
            except Exception:  # le visiteur recoit un message, le journal la pile
                logging.getLogger(journal.NOM).exception(
                    "echec", extra={"champs": {"trace": trace, "statut": 500}}
                )
                yield sse(Evenement("erreur", {"texte": textes.texte(q.langue, "erreur")}))
                yield sse(fin_publique(Evenement("fin", {"chemin": "erreur"}), trace, evaluation))
            if not evaluation:
                await c.journal_questions.garder(q.question, q.langue, chemin)

        return StreamingResponse(
            flux(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def app_depuis_env() -> FastAPI:
    """Point d'entree d'uvicorn : `uvicorn assistant.api:app_depuis_env --factory`."""
    return creer_app()
