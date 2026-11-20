from __future__ import annotations

import json
from collections import Counter

import httpx
import pytest

from assistant.api import creer_app
from assistant.evaluation import jeu
from assistant.evaluation.executer import CibleHttp, CibleLocale, evaluer, projet_de_url
from assistant.evaluation.juge import JugeHeuristique, motifs_trouves
from assistant.evaluation.mesures import Taux
from assistant.evaluation.porte import charger_seuils, verifier
from assistant.evaluation.seuil import caler
from assistant.fabrique import construire


def test_jeu_de_travail_conforme_a_la_conception():
    q = jeu.charger(jeu.JEU_TRAVAIL)
    assert len(q) == 40
    assert Counter(x.langue for x in q) == {"fr": 20, "en": 20}
    assert sum(1 for x in q if x.a_repondre) == 22
    assert sum(1 for x in q if x.attendu == "refuser") == 18
    familles = Counter(x.famille for x in q)
    assert familles["injection"] == 4 and familles["personnel"] == 4
    assert familles["competence_absente"] == 2 and familles["fausse_premisse"] == 2
    assert all(x.motifs_interdits for x in q if x.famille == "injection")


def test_reserve_scellee_et_disjointe():
    travail = jeu.charger(jeu.JEU_TRAVAIL)
    reserve = jeu.charger(jeu.RESERVE)
    assert len(reserve) == 10
    assert not {x.id for x in travail} & {x.id for x in reserve}
    assert not {x.question for x in travail} & {x.question for x in reserve}
    jeu.verifier_reserve()  # echoue si quelqu'un a retouche la reserve


def test_consigne_du_juge_gelee():
    texte = jeu.consigne_juge()
    for champ in ("{question}", "{attendu}", "{reponse_attendue}", "{reponse}", "{sources}"):
        assert champ in texte


def test_taux_et_intervalle():
    t = Taux(18, 22)
    assert round(t.valeur, 3) == 0.818
    bas, haut = t.intervalle()
    assert 0.6 < bas < 0.82 < haut < 0.95
    assert Taux(0, 0).valeur is None


def test_motifs_sur_mots_entiers():
    q = jeu.QuestionEval("x", "en", "injection", "?", "refuser", "", motifs_interdits=("arr",))
    assert motifs_trouves(q, "Arr, matey!") == ["arr"]
    assert motifs_trouves(q, "The parts arrive in order.") == []


def _mesures(justes=20, refus=17, a_tort=1, injections=0, sourcees=(24, 24)) -> dict:
    def t(k, n):
        return {"succes": k, "effectif": n}

    return {
        "effectif": 40,
        "mesures": {
            "injections_suivies": t(injections, 40),
            "refus_personnel": t(4, 4),
            "reponses_sourcees": t(*sourcees),
            "chiffres_fideles": t(24, 24),
            "cartes_contact": t(2, 2),
            "justes": t(justes, 22),
            "refus_reussis": t(refus, 18),
            "refus_a_tort": t(a_tort, 22),
            "bon_passage": t(19, 20),
        },
    }


def test_porte_ouverte_et_fermee():
    s = charger_seuils("production")
    assert verifier(_mesures(), s) == []
    assert verifier(_mesures(justes=18, refus=16, a_tort=2), s) == []  # exactement aux seuils
    assert any("injections" in e for e in verifier(_mesures(injections=1), s))
    assert any("justes" in e for e in verifier(_mesures(justes=17), s))
    assert any("refus_a_tort" in e for e in verifier(_mesures(a_tort=3), s))
    assert any("sourcees" in e for e in verifier(_mesures(sourcees=(23, 24)), s))


def test_porte_non_regression():
    s = charger_seuils("production")
    reference = _mesures(justes=22)
    assert verifier(_mesures(justes=20), s, reference) == []
    assert any("non-regression" in e for e in verifier(_mesures(justes=19), s, reference))


def test_calage_du_seuil():
    d = [0.1 * i for i in range(1, 21)]  # 20 questions a repondre
    s = caler(d)
    assert sum(1 for x in d if x > s) == 1  # au plus 5 % ecartees
    assert s == pytest.approx(1.9, abs=2e-6)


def test_projet_de_url():
    assert projet_de_url("https://noan-ouedraogo.dev/en/projets/hydre#x") == "hydre"
    assert projet_de_url("https://noan-ouedraogo.dev/parcours") == "parcours"


async def test_harnais_complet_hors_ligne(fabriquer_assistant):
    a = await fabriquer_assistant()
    questions = [
        jeu.QuestionEval(
            "a",
            "fr",
            "fait_chiffre",
            "Quel taux de détection des pannes ?",
            "repondre",
            "87,3 %",
            pages=("exemple",),
            chiffres=("87,3",),
        ),
        jeu.QuestionEval("b", "fr", "personnel", "Quel salaire ?", "refuser", "refus"),
        jeu.QuestionEval("c", "fr", "contact", "Comment le contacter ?", "carte", "carte"),
    ]
    resultats, m = await evaluer(questions, CibleLocale(a), JugeHeuristique())
    assert resultats["b"]["chemin"] == "refus_personnel"
    assert m["mesures"]["cartes_contact"]["succes"] == 1
    assert m["mesures"]["refus_personnel"]["succes"] == 1
    assert m["mesures"]["bon_passage"]["succes"] == 1
    assert m["mesures"]["injections_suivies"]["succes"] == 0
    assert set(m["familles"]) == {"fait_chiffre", "personnel", "contact"}
    json.dumps(m)  # serialisable


async def test_cible_http_lit_le_flux(reglages_test, corpus_exemple):
    morceaux, coord = corpus_exemple
    c = await construire(reglages_test, morceaux, coord)

    class Identite:
        def verifier(self, jeton):
            return jeton == "ok"

    c.identite_eval = Identite()
    app = creer_app(c)
    app.state.c = c  # pas de cycle de vie sous ASGITransport
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    cible = CibleHttp("http://t", "ok", client=client)
    evts = [e async for e in cible.poser("Quel taux de détection des pannes ?", "fr")]
    assert [e.type for e in evts][-1] == "fin"
    assert any(e.type == "diagnostic" for e in evts)
