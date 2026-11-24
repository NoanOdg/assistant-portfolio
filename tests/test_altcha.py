"""ALTCHA : defi signe, verification (juste, fausse, expiree, rejouee, forgee), CORS."""

from __future__ import annotations

import asyncio
import base64
import json
import time

import pytest
from fastapi.testclient import TestClient

from assistant import config
from assistant.api import creer_app, motif_origines
from assistant.fabrique import construire, construire_anti_robot
from assistant.session import Altcha, AntiRobotDesactive

from .conftest import ENV_TEST, resoudre

CLE = "cle-altcha-de-test-assez-longue"


def petit(**kw) -> Altcha:
    return Altcha(CLE, cout=10, **kw)


def modifier(charge: str, fonction) -> str:
    d = json.loads(base64.b64decode(charge))
    fonction(d)
    return base64.b64encode(json.dumps(d).encode()).decode()


# --- Defi ---------------------------------------------------------------------


def test_defi_signe_et_court():
    a = petit(expiration_s=120)
    avant = int(time.time())
    d = a.defi()
    p = d["parameters"]
    assert p["algorithm"] == "PBKDF2/SHA-256" and p["cost"] == 10 and p["keyPrefix"] == "00"
    assert avant + 120 <= p["expiresAt"] <= int(time.time()) + 120
    assert len(p["nonce"]) == 32 and len(p["salt"]) == 32
    assert len(d["signature"]) == 64  # HMAC-SHA-256 en hexa
    assert a.defi()["parameters"]["salt"] != p["salt"]  # un defi neuf a chaque appel


def test_cle_trop_courte():
    with pytest.raises(ValueError):
        Altcha("court")


# --- Verification -------------------------------------------------------------


async def test_solution_juste():
    a = petit()
    assert await a.verifier(resoudre(a.defi())) is None


async def test_reglages_reels_resolus():
    """Les parametres du depot (PBKDF2, 5 000 iterations, prefixe 00) passent."""
    r = config.charger(env={"ASSISTANT_SECRET_ALTCHA": CLE})
    a = construire_anti_robot(r)
    assert isinstance(a, Altcha) and a.cout == 5000
    assert await a.verifier(resoudre(a.defi())) is None


async def test_solution_fausse_puis_defi_grille():
    a = petit()
    juste = resoudre(a.defi())

    def fausser(d):
        d["solution"]["counter"] += 1

    assert await a.verifier(modifier(juste, fausser)) == "invalide"
    # Le defi a ete presente : meme la bonne solution ne passe plus.
    assert await a.verifier(juste) == "rejoue"


async def test_cle_derivee_fausse():
    a = petit()

    def fausser(d):
        d["solution"]["derivedKey"] = "00" * 32

    assert await a.verifier(modifier(resoudre(a.defi()), fausser)) == "invalide"


async def test_solution_expiree():
    a = petit(expiration_s=60, horloge=lambda: time.time() - 1000)
    assert await a.verifier(resoudre(a.defi())) == "expire"


async def test_solution_rejouee():
    a = petit()
    charge = resoudre(a.defi())
    assert await a.verifier(charge) is None
    assert await a.verifier(charge) == "rejoue"


async def test_envois_simultanes_un_seul_passe():
    a = petit()
    charge = resoudre(a.defi())
    resultats = await asyncio.gather(*(a.verifier(charge) for _ in range(5)))
    assert sorted(resultats, key=str) == [None, "rejoue", "rejoue", "rejoue", "rejoue"]


async def test_defi_forge_refuse_sans_griller_l_original():
    a = petit()
    juste = resoudre(a.defi())

    def facile(d):
        d["challenge"]["parameters"]["keyPrefix"] = ""

    assert await a.verifier(modifier(juste, facile)) == "invalide"
    # Signature fausse : rien n'est grille, la vraie solution passe encore.
    assert await a.verifier(juste) is None


async def test_defi_d_une_autre_cle():
    autre = Altcha("une-autre-cle-tout-aussi-longue", cout=10)
    assert await petit().verifier(resoudre(autre.defi())) == "invalide"


@pytest.mark.parametrize(
    "charge",
    ["", "pas du base64 !", base64.b64encode(b"{}").decode(), base64.b64encode(b"[1]").decode()],
)
async def test_charge_illisible(charge):
    a = petit()
    assert await a.verifier(charge) == "invalide"
    assert len(a.registre) == 0


def test_registre_purge_les_expires():
    t = [1000.0]
    a = Altcha(CLE, cout=10, horloge=lambda: t[0])
    assert a.registre.reserver("s1", 1100)
    assert not a.registre.reserver("s1", 1100)
    t[0] = 1200
    assert a.registre.reserver("s2", 1300)
    assert len(a.registre) == 1  # s1 est parti
    assert a.registre.reserver("s1", 1300)


async def test_anti_robot_desactive_sert_un_defi():
    d = AntiRobotDesactive()
    assert "signature" in d.defi()
    assert await d.verifier("n'importe quoi") is None


# --- Configuration ------------------------------------------------------------


def test_vertex_exige_la_cle_altcha():
    env = {
        "ASSISTANT_MOTEUR_MODE": "vertex",
        "ASSISTANT_MOTEUR_PROJET_GCP": "projet-test",
        "ASSISTANT_SECRET_SESSION": "s" * 32,
        "ASSISTANT_RECHERCHE_SEUIL_DISTANCE_VERTEX": "0.3",
    }
    with pytest.raises(ValueError, match="ALTCHA"):
        config.charger(env=env).verifier()
    config.charger(env={**env, "ASSISTANT_SECRET_ALTCHA": CLE}).verifier()


def test_anti_robot_inconnu_refuse():
    with pytest.raises(ValueError, match="anti-robots"):
        config.charger(env={"ASSISTANT_SECURITE_ANTI_ROBOT": "turnstile"}).verifier()


# --- API ----------------------------------------------------------------------


@pytest.fixture
async def composants(reglages_test, corpus_exemple):
    morceaux, coord = corpus_exemple
    reglages_test.securite["projet_pages"] = "portfolio-noan"
    return await construire(reglages_test, morceaux, coord)


@pytest.fixture
def client(composants):
    with TestClient(creer_app(composants)) as cl:
        yield cl


def test_get_defi(client):
    r = client.get("/defi")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert set(r.json()) == {"parameters", "signature"}


def test_session_par_altcha(client):
    charge = resoudre(client.get("/defi").json())
    r = client.post("/session", json={"altcha": charge})
    assert r.status_code == 200
    corps = r.json()
    assert corps["expire_dans"] == 1800 and corps["session"].count(".") == 1
    r = client.post("/session", json={"altcha": charge})
    assert r.status_code == 403 and r.json()["motif"] == "rejoue"


def test_session_refusee_et_journalisee(client, capsys):
    r = client.post("/session", json={"altcha": "faux"})
    assert r.status_code == 403
    assert r.json() == {"detail": "verification anti-robots refusee", "motif": "invalide"}
    lignes = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.startswith("{")]
    assert any(x["message"] == "anti_robot" and x["motif"] == "invalide" for x in lignes)


@pytest.mark.parametrize("corps", [{}, {"altcha": ""}, {"jeton": "x"}, {"altcha": "a" * 5000}])
def test_session_corps_invalide(client, corps):
    assert client.post("/session", json=corps).status_code == 422


def preflight(client, origine: str):
    return client.options(
        "/session",
        headers={
            "Origin": origine,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )


@pytest.mark.parametrize(
    "origine",
    [
        "https://noan-ouedraogo.dev",
        "https://3f2a9c1b.portfolio-noan.pages.dev",
        "https://refonte.portfolio-noan.pages.dev",
        "https://portfolio-noan.pages.dev",
        "http://localhost:4321",
    ],
)
def test_cors_origines_admises(client, origine):
    r = preflight(client, origine)
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == origine
    r = client.get("/defi", headers={"Origin": origine})
    assert r.headers["access-control-allow-origin"] == origine


@pytest.mark.parametrize(
    "origine",
    [
        "https://www.noan-ouedraogo.dev",
        "http://noan-ouedraogo.dev",
        "https://ailleurs.example",
        "https://noan-ouedraogo.dev.ailleurs.example",
        "https://autre-projet.pages.dev",
        "https://x.autre-projet.pages.dev",
        "https://portfolio-noan.pages.dev.ailleurs.example",
        "https://a.b.portfolio-noan.pages.dev",
        "http://refonte.portfolio-noan.pages.dev",
        "http://localhost:8080",
        "http://localhost:43210",
    ],
)
def test_cors_origines_refusees(client, origine):
    r = preflight(client, origine)
    assert r.status_code == 400
    assert "access-control-allow-origin" not in r.headers
    r = client.get("/defi", headers={"Origin": origine})
    assert "access-control-allow-origin" not in r.headers


def test_cors_sans_apercus_ni_local(tmp_path, corpus_exemple):
    r = config.charger(
        env={
            **ENV_TEST,
            "ASSISTANT_CORPUS_FICHIER": str(tmp_path / "corpus.jsonl"),
            "ASSISTANT_SECURITE_ORIGINES_LOCALES": "",
            "ASSISTANT_SECURITE_PROJET_PAGES": "",
        }
    )
    assert r.securite["projet_pages"] == "" and r.securite["origines_locales"] == []
    c = asyncio.run(construire(r, *corpus_exemple))
    with TestClient(creer_app(c)) as cl:
        assert preflight(cl, "http://localhost:4321").status_code == 400
        assert preflight(cl, "https://x.portfolio-noan.pages.dev").status_code == 400
        assert preflight(cl, "https://noan-ouedraogo.dev").status_code == 200


@pytest.mark.parametrize("nom", ["a.b", "a/b", "-a", "a b", "a*"])
def test_nom_de_projet_pages_invalide(nom):
    with pytest.raises(ValueError):
        motif_origines(nom)


def test_cors_en_tetes_du_widget(client):
    r = client.options(
        "/question",
        headers={
            "Origin": "https://noan-ouedraogo.dev",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization, content-type",
        },
    )
    assert r.status_code == 200
    autorises = r.headers["access-control-allow-headers"].lower()
    assert "authorization" in autorises and "content-type" in autorises
