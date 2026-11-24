from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from assistant.api import creer_app
from assistant.evaluation.executer import CibleHttp, resultat
from assistant.evaluation.jeu import QuestionEval
from assistant.fabrique import construire

from .conftest import ouvrir_session

# Adresses de documentation (RFC 5737) seulement.
IP = "203.0.113.77"
PROXY = "198.51.100.9"


class IdentiteFausse:
    def verifier(self, jeton: str) -> bool:
        return jeton == "jeton-eval-valide"


def lire_sse(texte: str) -> list[tuple[str, dict]]:
    evts = []
    for bloc in texte.strip().split("\n\n"):
        lignes = dict(ligne.split(": ", 1) for ligne in bloc.splitlines())
        evts.append((lignes["event"], json.loads(lignes["data"])))
    return evts


@pytest.fixture
async def composants(reglages_test, corpus_exemple):
    morceaux, coord = corpus_exemple
    c = await construire(reglages_test, morceaux, coord)
    c.identite_eval = IdentiteFausse()
    return c


@pytest.fixture
def client(composants):
    with TestClient(creer_app(composants)) as cl:
        yield cl


def session(client) -> dict:
    jeton = ouvrir_session(client)
    return {"Authorization": f"Bearer {jeton}", "X-Forwarded-For": f"{PROXY}, {IP}"}


def test_sante(client):
    assert client.get("/sante").json()["etat"] == "ok"


def test_question_en_flux_sse(client):
    r = client.post(
        "/question",
        json={"question": "Quel taux de détection des pannes ?", "langue": "fr"},
        headers=session(client),
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    evts = lire_sse(r.text)
    assert evts[-1][0] == "fin" and evts[-1][1]["trace"]
    assert "diagnostic" not in [t for t, _ in evts]  # reserve a l'evaluation


def test_sans_session_refuse(client):
    r = client.post("/question", json={"question": "Bonjour ?", "langue": "fr"})
    assert r.status_code == 401


def test_question_trop_longue(client):
    r = client.post(
        "/question", json={"question": "a" * 501, "langue": "fr"}, headers=session(client)
    )
    assert r.status_code == 422


def test_limite_par_minute(client):
    h = session(client)
    codes = [
        client.post(
            "/question", json={"question": "Le moniteur ?", "langue": "fr"}, headers=h
        ).status_code
        for _ in range(6)
    ]
    assert codes == [200] * 5 + [429]


def test_plafond_global(client, composants):
    composants.reglages.limites["questions_par_jour_global"] = 1
    h = session(client)
    assert (
        client.post("/question", json={"question": "Le moniteur ?"}, headers=h).status_code == 200
    )
    r = client.post("/question", json={"question": "Le moniteur ?"}, headers=h)
    assert r.status_code == 429 and "demain" in r.json()["texte"]


def test_evaluation_par_identite(client):
    r = client.post(
        "/question",
        json={"question": "Quel taux de détection des pannes ?"},
        headers={"X-Jeton-Evaluation": "jeton-eval-valide"},
    )
    assert "diagnostic" in [t for t, _ in lire_sse(r.text)]
    r = client.post(
        "/question", json={"question": "Le moniteur ?"}, headers={"X-Jeton-Evaluation": "faux"}
    )
    assert r.status_code == 401


def test_aucune_adresse_ip_dans_les_journaux(client, capsys):
    client.post(
        "/question",
        json={"question": "Quel taux de détection des pannes ?"},
        headers=session(client),
    )
    sortie = capsys.readouterr().out
    lignes = [json.loads(x) for x in sortie.splitlines() if x.startswith("{")]
    question = [x for x in lignes if x["message"] == "question"]
    assert question and question[0]["chemin"] == "reponse"
    assert IP not in sortie and PROXY not in sortie and "testclient" not in sortie
    assert "détection" not in sortie  # ni le texte de la question


def test_cors_limite_au_site(client):
    r = client.options(
        "/question",
        headers={"Origin": "https://ailleurs.example", "Access-Control-Request-Method": "POST"},
    )
    assert r.headers.get("access-control-allow-origin") is None
    r = client.options(
        "/question",
        headers={"Origin": "https://noan-ouedraogo.dev", "Access-Control-Request-Method": "POST"},
    )
    assert r.headers["access-control-allow-origin"] == "https://noan-ouedraogo.dev"


INTERNES = ("bilan", "chemin", "meilleure_distance", "cout_usd", "jetons_entree", "passages")


@pytest.mark.parametrize(
    ("question", "chemin"),
    [
        ("Quel taux de détection des pannes ?", "reponse"),
        ("Quelle est la recette de la tarte aux pommes ?", "refus_seuil"),
        ("Comment contacter Noan ?", "carte"),
    ],
)
def test_fin_publique_sans_statistiques_internes(client, capsys, question, chemin):
    r = client.post(
        "/question", json={"question": question, "langue": "fr"}, headers=session(client)
    )
    evts = lire_sse(r.text)
    assert evts[-1][0] == "fin" and set(evts[-1][1]) == {"trace"}
    assert not any(cle in r.text for cle in INTERNES)
    # Le journal, lui, garde le bilan complet.
    lignes = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.startswith("{")]
    q = next(x for x in lignes if x["message"] == "question")
    assert q["trace"] == evts[-1][1]["trace"] and q["chemin"] == chemin and "duree_ms" in q


def test_fin_complete_pour_l_evaluation(client):
    r = client.post(
        "/question",
        json={"question": "Quel taux de détection des pannes ?"},
        headers={"X-Jeton-Evaluation": "jeton-eval-valide"},
    )
    fin = lire_sse(r.text)[-1][1]
    assert fin["chemin"] == "reponse" and fin["trace"]
    assert fin["bilan"]["meilleure_distance"] is not None and "cout_usd" in fin["bilan"]


async def test_cible_http_de_l_evaluation_lit_le_bilan(composants):
    """assistant-evaluer --cible http obtient toujours chemin, cout et distance."""
    app = creer_app(composants)
    app.state.c = composants
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://service") as cl:
        cible = CibleHttp("http://service", "jeton-eval-valide", client=cl)
        q = QuestionEval(
            id="t1",
            langue="fr",
            famille="fait",
            question="Quel taux de détection des pannes ?",
            attendu="reponse",
            reponse_attendue="",
        )
        res = await resultat(cible, q)
    assert res["chemin"] != "inconnu"
    assert res["duree_ms"] is not None and "cout_usd" in res and "meilleure_distance" in res
