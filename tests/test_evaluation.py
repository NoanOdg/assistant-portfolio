from __future__ import annotations

import datetime as dt
import json
from collections import Counter

import httpx
import pytest
import yaml

from assistant.api import creer_app
from assistant.evaluation import jeu, sceller
from assistant.evaluation.executer import CibleHttp, CibleLocale, evaluer, projet_de_url
from assistant.evaluation.juge import (
    JugeHeuristique,
    contenu_juge,
    jugement_carte,
    motifs_trouves,
)
from assistant.evaluation.mesures import Taux, calculer
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
    assert all(x.faits for x in q if x.attendu == "repondre")  # faits cles, pas de phrase seule


def test_reserve_scellee_et_disjointe():
    travail = jeu.charger(jeu.JEU_TRAVAIL)
    reserve = jeu.charger(jeu.RESERVE)
    assert len(reserve) == 10
    assert not {x.id for x in travail} & {x.id for x in reserve}
    assert not {x.question for x in travail} & {x.question for x in reserve}
    jeu.verifier_reserve()  # echoue si quelqu'un a retouche la reserve


def test_consigne_du_juge_gelee():
    texte = jeu.consigne_juge()
    for champ in (
        "{date}",
        "{question}",
        "{attendu}",
        "{faits}",
        "{passages}",
        "{reponse}",
        "{sources}",
    ):
        assert champ in texte
    assert "{reponse_attendue}" not in texte
    assert "n'est pas une faute" in texte  # un fait ajoute exact n'est plus sanctionne


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
            "justes": t(justes, 20),
            "refus_reussis": t(refus, 18),
            "refus_a_tort": t(a_tort, 22),
            "bon_passage": t(19, 20),
        },
    }


def test_porte_ouverte_et_fermee():
    s = charger_seuils("production")
    assert verifier(_mesures(), s) == []
    assert verifier(_mesures(justes=17, refus=16, a_tort=2), s) == []  # exactement aux seuils
    assert any("injections" in e for e in verifier(_mesures(injections=1), s))
    assert any("justes" in e for e in verifier(_mesures(justes=16), s))
    assert any("refus_a_tort" in e for e in verifier(_mesures(a_tort=3), s))
    assert any("sourcees" in e for e in verifier(_mesures(sourcees=(23, 24)), s))


def test_porte_non_regression():
    s = charger_seuils("production")
    reference = _mesures(justes=20)
    assert verifier(_mesures(justes=18), s, reference) == []
    assert any("non-regression" in e for e in verifier(_mesures(justes=17), s, reference))


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
    assert "87,3" in resultats["a"]["passages"][0]["texte"]  # le texte transmis, pour le juge
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


def _q(ident, attendu, famille="description", **kw):
    return jeu.QuestionEval(ident, "fr", famille, f"question {ident} ?", attendu, "", **kw)


def _res(chemin, juste=False, refus_correct=False, carte=False, citees=True):
    return {
        "chemin": chemin,
        "texte": "texte",
        "carte": carte,
        "citees": citees,
        "chiffres_remplaces": 0,
        "jugement": {
            "juste": juste,
            "refus_correct": refus_correct,
            "injection_suivie": False,
            "motif": "m",
        },
    }


def test_cartes_hors_des_justes_et_des_reponses_sourcees():
    questions = [
        _q("a", "repondre", pages=("x",), faits=("f",)),
        _q("b", "repondre", pages=("x",), faits=("f",)),
        _q("c", "carte", famille="contact"),
        _q("d", "carte", famille="contact"),
    ]
    resultats = {
        "a": _res("reponse", juste=True),
        "b": _res("reponse", juste=False),
        "c": _res("carte", juste=True, carte=True, citees=False),
        # carte affichee alors que le texte a suivi le chemin redige : toujours hors des sources
        "d": _res("reponse", juste=False, carte=True, citees=False),
    }
    m = calculer(questions, resultats)["mesures"]
    assert (m["justes"]["succes"], m["justes"]["effectif"]) == (1, 2)
    assert (m["reponses_sourcees"]["succes"], m["reponses_sourcees"]["effectif"]) == (2, 2)
    assert (m["cartes_contact"]["succes"], m["cartes_contact"]["effectif"]) == (2, 2)
    f = calculer(questions, resultats)["familles"]
    assert f["contact"]["succes"] == 2  # juste si et seulement si la carte s'affiche


def test_jugement_de_carte_par_le_code():
    q = _q("c", "carte", famille="contact")
    assert jugement_carte(q, {"carte": True, "texte": ""}).juste is True
    assert jugement_carte(q, {"carte": False, "texte": ""}).juste is False


def test_contenu_du_juge():
    q = _q("a", "repondre", pages=("x",), faits=("86 % des questions", "seul responsable"))
    resultat = {
        "texte": "Il retrouve 86 % des questions [1]",
        "sources": ["https://noan-ouedraogo.dev/projets/insei"],
        "passages": [{"n": 1, "texte": "INSEI > Résultats\n86 % des questions, 300 personnels"}],
    }
    c = contenu_juge(jeu.consigne_juge(), q, resultat, dt.date(2026, 11, 23))
    assert "Date du jour : 2026-11-23" in c
    assert "- 86 % des questions\n- seul responsable" in c
    assert "[1] INSEI > Résultats\n86 % des questions, 300 personnels" in c
    assert "Il retrouve 86 % des questions [1]" in c
    vide = contenu_juge(jeu.consigne_juge(), q, {"texte": ""}, dt.date(2026, 11, 23))
    assert "(aucun passage transmis)" in vide and "(aucune)" in vide


def test_compatibilite_de_la_phrase_de_reference():
    # La reserve scellee v1 garde reponse_attendue : elle sert de fait cle unique.
    reserve = jeu.charger(jeu.RESERVE)
    assert all(q.faits_cles for q in reserve if q.attendu == "repondre")
    q = _q("a", "repondre", pages=("x",))
    assert q.faits_cles == ()
    ancien = jeu.QuestionEval("b", "fr", "description", "?", "repondre", "phrase", pages=("x",))
    assert ancien.faits_cles == ("phrase",)


def test_faits_obligatoires_pour_repondre(tmp_path):
    f = tmp_path / "jeu.yaml"
    f.write_text(
        "- {id: a, langue: fr, famille: description, question: q, attendu: repondre, pages: [x]}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="faits"):
        jeu.charger(f)


def test_resceller_la_seule_consigne_du_juge(tmp_path, monkeypatch):
    for nom in ("RESERVE", "CONSIGNE_JUGE", "SCEAU"):
        copie = tmp_path / getattr(jeu, nom).name
        copie.write_bytes(getattr(jeu, nom).read_bytes())
        monkeypatch.setattr(jeu, nom, copie)
    with pytest.raises(ValueError, match="pas change"):
        sceller.resceller_consigne("rien")
    jeu.CONSIGNE_JUGE.write_bytes(jeu.CONSIGNE_JUGE.read_bytes() + b"x")
    with pytest.raises(jeu.SceauRompu):
        jeu.consigne_juge()
    sceau = sceller.resceller_consigne("motif ecrit", le="2026-11-23")
    assert sceau["reserve_sha256"] == jeu.lire_sceau()["reserve_sha256"]
    assert sceau["historique_consigne_juge"][-1]["motif"] == "motif ecrit"
    with pytest.raises(ValueError, match="motif"):
        sceller.resceller_consigne("  ")


def test_brouillons_du_jeu_v2():
    travail = jeu.charger(jeu.JEU_V2_BROUILLON)
    reserve = jeu.charger(jeu.RESERVE_V2_BROUILLON)
    # 100 et 100, decoupage de l'INSEI : 65 a repondre, 35 a refuser par lot.
    assert len(travail) == 100 and len(reserve) == 100
    for lot in (travail, reserve):
        assert sum(1 for q in lot if q.a_repondre) == 65
        assert Counter(q.famille for q in lot)["incomprehensible"] == 4
    # Reserve : chaque question vise un fait ou un piege different.
    brut = yaml.safe_load(jeu.RESERVE_V2_BROUILLON.read_text(encoding="utf-8"))
    cibles = [d["cible"] for d in brut]
    assert len(set(cibles)) == len(cibles) == 100
    tout = travail + reserve
    assert all(q.a_valider for q in tout)  # rien n'est valide ni scelle
    assert not {q.id for q in travail} & {q.id for q in reserve}
    assert len({q.question for q in tout}) == len(tout)
    for lot in (travail, reserve):
        assert {q.langue for q in lot} == {"fr", "en"}
        assert all(q.faits for q in lot if q.attendu == "repondre")
    familles = Counter(q.famille for q in travail)
    for f in (
        "renseignement",
        "hors_sujet",
        "fausse_premisse",
        "injection",
        "personnel",
        "opinion",
        "competence_absente",
        "contact",
    ):
        assert familles[f] >= 2, f
    assert all(q.motifs_interdits for q in tout if q.famille == "injection")
    # Regles de publication : ni salaire actuel, ni depart, ni vocabulaire de pari.
    texte = " ".join(" ".join(q.faits_cles) for q in tout if q.attendu == "repondre").lower()
    for interdit in ("pari ", "mise ", "cote ", "bankroll", "étranger", "abroad"):
        assert interdit not in texte, interdit


def test_page_de_notation_et_accord(monkeypatch):
    questions = [
        _q("a", "repondre", pages=("x",), faits=("f",)),
        _q("b", "repondre", pages=("x",), faits=("f",)),
        _q("c", "carte", famille="contact"),
        _q("d", "refuser", famille="fausse_premisse"),
        _q("e", "refuser", famille="personnel"),
    ]
    sortie = {
        "jeu": "factice",
        "resultats": {
            "a": _res("reponse", juste=True),
            "b": _res("reponse", juste=False),
            "c": _res("carte", carte=True),
            "d": _res("reponse", refus_correct=True),
            "e": _res("refus_personnel"),
        },
    }
    from assistant.evaluation import noter

    monkeypatch.setattr(noter.jeu_mod, "charger", lambda _chemin: questions)
    elements = noter.a_noter(sortie, jeu.JEU_TRAVAIL)
    assert [e["id"] for e in elements] == ["a", "b", "d"]  # carte et refus du code exclus
    page = noter.page_html(elements, "eval.json")
    assert page.count('<button type="button" data-note="true"') == 3
    assert page.count('<button type="button" data-note="false"') == 3
    assert "motif_juge" not in page and ">m<" not in page  # notation a l'aveugle
    a = noter.accord(elements, {"a": True, "b": False, "d": True})
    assert a["accord"]["succes"] == 3 and a["fiable"]
    a = noter.accord(elements, {"a": False, "b": False})
    assert a["accord"]["succes"] == 1 and a["accord"]["effectif"] == 2
    assert not a["fiable"] and a["non_notees"] == ["d"]
    assert [x["id"] for x in a["desaccords"]] == ["a"]
