"""API HTTP : FastAPI, reponses en flux SSE.

    POST /session   jeton Turnstile -> jeton de session signe (30 min)
    POST /question  question + langue de la page -> flux d'evenements SSE
    GET  /sante     sonde de vie et de disponibilite

Evenements du flux : etape, texte, sources, carte, refus, erreur, fin
(et diagnostic, pour l'evaluation seulement).
"""

from __future__ import annotations

import json
import logging
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
    jeton: str = Field(default="", max_length=4096)


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    langue: Literal["fr", "en"] = "fr"


def sse(e: Evenement) -> str:
    return f"event: {e.type}\ndata: {json.dumps(e.donnees, ensure_ascii=False)}\n\n"


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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(reglages.securite["origines"]),
        allow_methods=["POST", "GET"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )

    @app.get("/sante")
    async def sante() -> dict:
        return {"etat": "ok", "version": VERSION}

    @app.post("/session")
    async def session(demande: DemandeSession, request: Request) -> dict:
        c: Composants = request.app.state.c
        if not await c.anti_robot.verifier(demande.jeton):
            raise HTTPException(status_code=403, detail="verification anti-robots refusee")
        return {"session": c.sessions.emettre(), "expire_dans": c.sessions.duree_s}

    @app.post("/question")
    async def question(
        q: Question,
        request: Request,
        authorization: str = Header(default=""),
        x_jeton_evaluation: str = Header(default=""),
        x_cloud_trace_context: str = Header(default=""),
    ):
        c: Composants = request.app.state.c
        r = c.reglages
        evaluation = bool(x_jeton_evaluation) and c.identite_eval.verifier(x_jeton_evaluation)
        if not evaluation:
            jeton = authorization.removeprefix("Bearer ").strip()
            try:
                c.sessions.verifier(jeton)
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

        trace = uuid.uuid4().hex
        trace_gcp = ""
        if x_cloud_trace_context and r.moteur["projet_gcp"]:
            trace_gcp = (
                f"projects/{r.moteur['projet_gcp']}/traces/{x_cloud_trace_context.split('/')[0]}"
            )

        async def flux() -> AsyncIterator[str]:
            chemin = "erreur"
            try:
                async for e in c.assistant.repondre(q.question, q.langue, diagnostic=evaluation):
                    if e.type == "fin":
                        chemin = e.donnees["chemin"]
                        e.donnees["trace"] = trace
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
                    yield sse(e)
            except Exception:  # le visiteur recoit un message, le journal la pile
                logging.getLogger(journal.NOM).exception(
                    "echec", extra={"champs": {"trace": trace, "statut": 500}}
                )
                yield sse(Evenement("erreur", {"texte": textes.texte(q.langue, "erreur")}))
                yield sse(Evenement("fin", {"chemin": "erreur", "trace": trace}))
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
