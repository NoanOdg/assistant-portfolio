"""Instrument de la comparaison (etude du 24/11/2026, partie c, points 12 a 19)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import httpx
import pytest
import yaml

from assistant import config
from assistant.corpus.ingestion import ecrire
from assistant.evaluation import aiguillage, comparaison, jeu, rappel, sceller
from assistant.evaluation.executer import evaluer
from assistant.evaluation.executer import main as evaluer_main
from assistant.evaluation.juge import (
    IndexCorpus,
    JugeHeuristique,
    Jugement,
    contenu_juge_v2,
    reference,
)
from assistant.evaluation.mesures import calculer
from assistant.evaluation.rejouer import rejuger, simuler_seuil
from assistant.evaluation.reprise import ErreurTechnique, Reprise, est_passagere
from assistant.modeles import Evenement, Morceau, empreinte

RACINE = config.RACINE


def q(ident, attendu="repondre", famille="description", **kw) -> jeu.QuestionEval:
    if attendu == "repondre":
        kw.setdefault("pages", ("exemple",))
        kw.setdefault("faits", ("un fait",))
    return jeu.QuestionEval(
        ident, kw.pop("langue", "fr"), famille, f"Question {ident} ?", attendu, "", **kw
    )


def m(i, texte, projet="exemple", langue="fr") -> Morceau:
    return Morceau(
        f"m{i}",
        f"/{projet}",
        f"s{i}",
        projet,
        f"{projet} > {i}",
        texte,
        langue,
        projet,
        "prose",
        empreinte(texte),
    )


# Reprise sur 429 et 5xx (D5).


class Erreur(Exception):
    def __init__(self, code):
        super().__init__(f"code {code}")
        self.code = code


def test_erreurs_passageres():
    assert est_passagere(Erreur(429)) and est_passagere(Erreur(503))
    assert not est_passagere(Erreur(400)) and not est_passagere(ValueError("bogue"))
    requete = httpx.Request("GET", "https://exemple.test")
    assert est_passagere(httpx.ConnectError("coupure", request=requete))
    reponse = httpx.Response(429, request=requete)
    assert est_passagere(httpx.HTTPStatusError("trop", request=requete, response=reponse))


async def test_reprise_attente_exponentielle():
    attentes, essais = [], []

    async def dormir(s):
        attentes.append(s)

    async def appel():
        essais.append(1)
        if len(essais) < 4:
            raise Erreur(429)
        return "ok"

    r = Reprise(tentatives=6, attente_s=2, plafond_s=5, dormir=dormir, hasard=lambda: 0.0)
    assert await r.jouer(appel) == "ok"
    assert attentes == [2, 4, 5]  # double, puis plafonne

    async def bogue():
        raise ValueError("bogue")

    with pytest.raises(ErreurTechnique):
        await r.jouer(bogue)
    assert len(attentes) == 3  # une erreur non passagere n'est pas rejouee


class CibleCapricieuse:
    """Renvoie 429 aux premiers appels pour certaines questions."""

    def __init__(self, echecs: dict[str, int]) -> None:
        self.echecs = echecs
        self.appels: dict[str, int] = {}

    async def poser(self, question, langue):
        ident = question.split()[1]
        self.appels[ident] = self.appels.get(ident, 0) + 1
        if self.appels[ident] <= self.echecs.get(ident, 0):
            raise Erreur(429)
        yield Evenement("texte", {"texte": "Réponse [1]"})
        yield Evenement("sources", {"citees": True, "sources": [{"url": "https://x/exemple"}]})
        yield Evenement("fin", {"chemin": "reponse", "bilan": {"cout_usd": 0.001}})


async def test_une_erreur_ne_fait_plus_planter_le_passage():
    async def dormir(_s):
        return None

    questions = [q("a"), q("b"), q("c")]
    cible = CibleCapricieuse({"a": 1, "b": 100})
    resultats, mesures = await evaluer(
        questions, cible, JugeHeuristique(), 2, Reprise(tentatives=3, dormir=dormir)
    )
    assert resultats["a"]["chemin"] == "reponse"  # rejouee apres un 429
    assert resultats["b"]["chemin"] == "erreur_technique"
    assert cible.appels["b"] == 6  # 3 essais, puis rejouee en fin de passage (3 essais)
    assert mesures["erreurs_techniques"] == ["b"]
    assert mesures["effectif"] == 2  # jamais comptee comme fausse


# Juge v2 : reference fixe (D6).


def test_reference_fixe_quelle_que_soit_l_architecture():
    corpus = [
        m(1, "Le moniteur détecte 87,3 % des pannes."),
        m(2, "Page anglaise", langue="en"),
        m(3, "Autre projet", projet="autre"),
        m(4, "Hors des pages attendues, mais cité.", projet="ailleurs"),
    ]
    index = IndexCorpus(corpus)
    question = q("a", pages=("exemple",))
    cites = [{"n": 9, "id": "m4", "texte": ""}]
    b = {"passages": [{"n": 1, "id": "m1"}], "passages_cites": cites, "texte": "87,3 % [9]"}
    c = {
        "passages": [{"n": i, "id": f"m{i}"} for i in range(1, 40)],
        "architecture": "C",
        "passages_cites": cites,
        "texte": "87,3 % [9]",
    }
    assert reference(question, b, index) == reference(question, c, index)
    ref = reference(question, b, index)
    assert "87,3 %" in ref and "Page anglaise" not in ref and "Autre projet" not in ref
    assert "Hors des pages attendues" in ref
    consigne = jeu.CONSIGNE_JUGE_V2.read_text(encoding="utf-8")
    contenu_c = contenu_juge_v2(consigne, question, c, dt.date(2026, 11, 24), index)
    assert contenu_c.index("Référence :") < contenu_c.index("Question :")
    assert "tout le site de la langue a été transmis" in contenu_c
    contenu_b = contenu_juge_v2(consigne, question, b, dt.date(2026, 11, 24), index)
    assert "[1] exemple > 1" in contenu_b


def test_consigne_v2_scellee_a_part(tmp_path, monkeypatch):
    for nom in ("RESERVE", "CONSIGNE_JUGE", "CONSIGNE_JUGE_V2", "SCEAU", "REGLES"):
        copie = tmp_path / getattr(jeu, nom).name
        copie.write_bytes(getattr(jeu, nom).read_bytes())
        monkeypatch.setattr(jeu, nom, copie)
    sceau = jeu.lire_sceau()
    sceau.pop("consigne_juge_v2_sha256", None)
    sceau.pop("regles_decision_sha256", None)
    jeu.SCEAU.write_text(json.dumps(sceau), encoding="utf-8")
    with pytest.raises(jeu.SceauRompu, match="pas scellee"):
        jeu.consigne_juge("v2")
    nouveau = sceller.resceller_consigne("reference fixe 11b", le="2026-11-24", version="v2")
    jeu.SCEAU.write_text(json.dumps(nouveau), encoding="utf-8")
    assert "{reference}" in jeu.consigne_juge("v2")
    assert nouveau["consigne_juge_sha256"] == sceau["consigne_juge_sha256"]  # v1 intacte
    assert nouveau["reserve_sha256"] == sceau["reserve_sha256"]  # reserve intacte
    assert nouveau["historique_consigne_juge_v2"][-1]["ancienne_sha256"] is None
    # Regles de decision : non scellees, scellees, modifiees.
    assert comparaison.statut_regles(jeu.REGLES) == "non scellees"
    jeu.SCEAU.write_text(json.dumps(sceller.sceller_regles("avant 0.2")), encoding="utf-8")
    assert comparaison.statut_regles(jeu.REGLES) == "scellees"
    jeu.REGLES.write_bytes(jeu.REGLES.read_bytes() + b"\n# retouche\n")
    assert comparaison.statut_regles(jeu.REGLES) == "modifiees depuis le sceau"


def test_la_consigne_v1_reste_scellee():
    jeu.consigne_juge()  # la porte de la CI joue toujours v1


# Mesures, sorties synthetiques et regle R0.

QUESTIONS = (
    [q(f"r{i:02d}") for i in range(1, 21)]
    + [q(f"x{i:02d}", "refuser", "hors_sujet") for i in range(1, 11)]
    + [q("c01", "carte", "contact"), q("p01", "refuser", "personnel")]
)


def sortie(
    justes: int,
    cout: float = 0.001,
    premier: float = 800.0,
    refus_a_tort: int = 0,
    refus_rates: int = 0,
    archi: str = "B",
    effective: dict | None = None,
    jeu_empreinte: str = "j1",
    erreurs: tuple[str, ...] = (),
) -> dict:
    """Un passage : `justes` questions a repondre justes, les autres fausses ou refusees a tort."""
    resultats = {}
    for i, x in enumerate(QUESTIONS):
        base = {
            "id": x.id,
            "attendu": x.attendu,
            "famille": x.famille,
            "groupe": x.id,
            "langue": "fr",
            "texte": "Texte [1]",
            "sources": ["https://x/exemple"],
            "citees": True,
            "carte": False,
            "chiffres_remplaces": 0,
            "cout_usd": cout / 2,
            "cout_production_usd": cout,
            "premier_mot_ms": premier,
            "chemin": "reponse",
            "jugement": Jugement(False, False, False, "").vers_dict(),
        }
        if x.id in erreurs:
            base["chemin"] = "erreur_technique"
        elif x.attendu == "repondre":
            rang = int(x.id[1:])
            if rang <= justes:
                base["jugement"]["juste"] = True
            elif rang > 20 - refus_a_tort:
                base["chemin"] = "refus_modele"
        elif x.attendu == "carte":
            base.update(carte=True, chemin="carte", premier_mot_ms=None)
        elif x.famille == "personnel":
            base["chemin"] = "refus_personnel"
        else:
            base["chemin"] = "reponse" if i - 20 < refus_rates else "refus_modele"
        resultats[x.id] = base
    return {
        "architecture": archi,
        "config": {"effective": effective or {}},
        "empreintes": {
            "jeu": jeu_empreinte,
            "juge": "juge",
            "corpus_source": "corpus",
            "config": archi,
        },
        "mesures": calculer(QUESTIONS, resultats),
        "resultats": resultats,
    }


def test_mesures_reussites_et_couts():
    s = sortie(15, cout=0.002)
    mm = s["mesures"]
    assert mm["mesures"]["reussites"] == {
        **mm["mesures"]["reussites"],
        "succes": 26,
        "effectif": 31,
    }
    assert mm["cout_usd"]["production_par_question"] == pytest.approx(0.002)
    assert mm["cout_usd"]["par_question"] == pytest.approx(0.001)
    assert (
        mm["mesures"]["chiffres_attribues"]["succes"]
        == mm["mesures"]["chiffres_attribues"]["effectif"]
    )


def regles() -> dict:
    return comparaison.charger_regles()


def resume(nom, passages):
    return comparaison.resumer(nom, passages, regles())


def test_bruit_et_g():
    passages = [sortie(j) for j in (10, 12, 14, 10, 9)]
    b = comparaison.bruit(passages, regles())
    assert b["sigma"] == pytest.approx(2.0)  # reussites 21, 23, 25, 21, 20
    assert b["G"] == 4  # plafond(2 x 2) = 4 > 3
    calme = comparaison.bruit([sortie(10)] * 5, regles())
    assert calme["G"] == 3 and calme["assez_de_passages"]
    with pytest.raises(ValueError):
        comparaison.bruit([sortie(10)], regles())


@pytest.mark.parametrize(
    ("challenger", "decision", "condition"),
    [
        (lambda: [sortie(14)] * 2, "remplace", None),
        (lambda: [sortie(12)] * 2, "garde_la_base", None),  # egalite, meme cout, meme complexite
        (lambda: [sortie(11, cout=0.0005)] * 2, "remplace", None),  # egalite, moins cher
        (lambda: [sortie(11, archi="C", cout=0.0011)] * 2, "remplace", None),  # 20 %, plus simple
        (lambda: [sortie(11, archi="A", cout=0.0011)] * 2, "garde_la_base", None),
        (lambda: [sortie(14, refus_a_tort=2)] * 2, "garde_la_base", "3."),
        (lambda: [sortie(17, refus_rates=4)] * 2, "garde_la_base", "4."),
        (lambda: [sortie(14, cout=0.013)] * 2, "garde_la_base", "5."),
        (lambda: [sortie(14, premier=3500)] * 2, "garde_la_base", "6."),
        (lambda: [sortie(14, erreurs=("r01",))] * 2, "a_rejouer", None),
        (lambda: [sortie(14, jeu_empreinte="autre")] * 2, "non_comparable", None),
    ],
)
def test_regle_r0(challenger, decision, condition):
    base = resume("base", [sortie(11)] * 5)
    v = comparaison.r0(base, resume("challenger", challenger()), 3, regles())
    assert v["decision"] == decision, v
    if condition:
        assert any(c["nom"].startswith(condition) and not c["tenue"] for c in v["conditions"])


def test_garde_fous_du_code_bloquent():
    s = sortie(14)
    s["resultats"]["c01"]["carte"] = False
    s["mesures"] = calculer(QUESTIONS, s["resultats"])
    v = comparaison.r0(resume("base", [sortie(11)]), resume("c", [s, s]), 3, regles())
    assert v["decision"] == "garde_la_base"
    assert not v["conditions"][0]["tenue"] and "cartes_contact" in v["conditions"][0]["detail"]


def test_complexite_lue_dans_la_configuration():
    r = regles()
    assert comparaison.complexite(sortie(1, archi="C"), r) < comparaison.complexite(sortie(1), r)
    avec = sortie(1, archi="C", effective={"refus": {"prefiltre_seuil": True}})
    assert comparaison.complexite(avec, r) == (0, 1)
    simule = {**sortie(1, archi="C"), "simulation": {"prefiltre_seuil": True}}
    assert comparaison.complexite(simule, r) == (0, 1)


def test_regle_d_architecture():
    r = regles()
    b = resume("B", [sortie(11)] * 2)
    c = resume("C", [sortie(16, archi="C", cout=0.009)] * 2)
    a = resume("A", [sortie(12, archi="A", cout=0.003)] * 2)
    assert comparaison.architecture([b, c, a], 3, r)["gagnante"] == "C"
    # Egalite a moins de G : la moins chere, puis la plus simple a 20 %.
    c2 = resume("C", [sortie(12, archi="C", cout=0.009)] * 2)
    assert comparaison.architecture([b, c2, a], 3, r)["gagnante"] == "B"
    c3 = resume("C", [sortie(12, archi="C", cout=0.0011)] * 2)
    assert comparaison.architecture([b, c3, a], 3, r)["gagnante"] == "C"
    # Au-dessus du plafond de cout : ecartee, quelle que soit sa reussite.
    chere = resume("C", [sortie(20, archi="C", cout=0.05)] * 2)
    v = comparaison.architecture([b, chere], 3, r)
    assert v["gagnante"] == "B" and "C" in v["ecartees"]


def test_information_mcnemar_wilson_bootstrap():
    base = {f"q{i}": 0.0 for i in range(10)}
    chal = {**base, **{f"q{i}": 1.0 for i in range(5)}}
    mc = comparaison.mcnemar_exact(base, chal)
    assert (mc["base_seule"], mc["challenger_seul"], mc["p"]) == (0, 5, 0.0625)
    groupes = {f"q{i}": "g" if i < 3 else f"q{i}" for i in range(10)}
    ic1 = comparaison.bootstrap_groupe(base, chal, groupes, 500, 7)
    assert ic1 == comparaison.bootstrap_groupe(base, chal, groupes, 500, 7)  # graine fixe
    assert ic1[0] <= 5 <= ic1[1]
    assert comparaison.wilson(resume("b", [sortie(11)]))[0] < 22 / 31


def test_rapport_et_cli(tmp_path, capsys):
    for k in range(1, 6):
        (tmp_path / f"b0-r{k}.json").write_text(json.dumps(sortie(10 + k % 2)), encoding="utf-8")
    for k in (1, 2):
        (tmp_path / f"c0-r{k}.json").write_text(json.dumps(sortie(15, archi="C")), encoding="utf-8")
    assert (
        comparaison.main_bruit(
            [str(tmp_path / "b0-r*.json"), "--sortie", str(tmp_path / "bruit.json")]
        )
        == 0
    )
    code = comparaison.main_comparer(
        [
            "--base",
            str(tmp_path / "b0-r*.json"),
            "--challenger",
            str(tmp_path / "c0-r*.json"),
            "--bruit",
            str(tmp_path / "bruit.json"),
            "--rapport",
            str(tmp_path / "rapport.md"),
            "--sortie",
            str(tmp_path / "verdict.json"),
        ]
    )
    assert code == 0
    texte = (tmp_path / "rapport.md").read_text(encoding="utf-8")
    assert "Decision : remplace" in texte and "McNemar" in texte
    verdict = json.loads((tmp_path / "verdict.json").read_text(encoding="utf-8"))
    assert verdict["G"] == 3
    comparaison.main_comparer(
        ["--architecture", f"B={tmp_path / 'b0-r*.json'}", f"C={tmp_path / 'c0-r*.json'}"]
    )
    assert "Gagnante : C" in capsys.readouterr().out


# Rejeux sans regenerer.


def test_simuler_le_seuil_sur_un_passage_de_c():
    s = sortie(15, archi="C")
    s["resultats"]["r01"]["meilleure_distance"] = 0.9  # juste, mais au-dessus du seuil
    s["resultats"]["x01"]["meilleure_distance"] = 0.9  # a refuser : deja refusee
    s["resultats"]["x10"]["meilleure_distance"] = 0.1
    s["resultats"]["c01"]["meilleure_distance"] = 0.9  # carte du code : intouchable
    n = simuler_seuil(s, QUESTIONS, 0.5)
    assert n["simulation"]["refusees"] == ["r01", "x01"]
    assert (
        n["resultats"]["r01"]["chemin"] == "refus_seuil" and n["resultats"]["r01"]["cout_usd"] == 0
    )
    assert n["resultats"]["c01"]["chemin"] == "carte"
    assert n["mesures"]["mesures"]["refus_a_tort"]["succes"] == 1
    assert s["resultats"]["r01"]["chemin"] == "reponse"  # la sortie d'origine ne bouge pas


async def test_rejuger_sans_regenerer():
    s = sortie(15)
    s["juge"], s["juge_version"] = "ancien", "v1"
    n = await rejuger(s, QUESTIONS, JugeHeuristique())
    assert n["rejuge"]["ancien_juge"] == "ancien" and n["juge"] == "heuristique-v1"
    assert n["resultats"]["r01"]["texte"] == s["resultats"]["r01"]["texte"]
    assert (
        n["mesures"]["mesures"]["justes"]["succes"] != s["mesures"]["mesures"]["justes"]["succes"]
    )


# Passages d'or, rappel hors ligne.


class LecteurFaux:
    nom = "faux-or"

    async def designer(self, contenu):
        return {"faits": [{"fait": "un fait", "sections": [2, 99]}]}


async def test_passages_d_or():
    corpus = [m(1, "Un"), m(2, "Deux"), m(3, "Three", langue="en")]
    questions = [q("a"), q("b", "refuser", "hors_sujet")]
    consigne = jeu.CONSIGNE_OR.read_text(encoding="utf-8")
    gold = await rappel.passages_or(questions, corpus, LecteurFaux(), consigne)
    assert list(gold) == ["a"]
    assert gold["a"]["faits"][0]["passages"] == [{"id": "m2", "url": "/exemple", "ancre": "s2"}]
    assert "[2] exemple > 2" in rappel.contenu_or(consigne, questions[0], corpus[:2])
    assert len(rappel.a_controler([f"q{i}" for i in range(40)])) == 15
    assert rappel.a_controler(["a", "b"]) == ["a", "b"]


def test_rappel_et_aire_roc():
    corpus = [m(1, "Un"), m(2, "Deux"), m(3, "Trois")]
    questions = [q("a"), q("b"), q("x", "refuser", "hors_sujet")]
    gold = {
        "a": {
            "faits": [{"fait": "f", "passages": [{"id": "m2", "url": "/exemple", "ancre": "s2"}]}]
        },
        "b": {
            "faits": [{"fait": "f", "passages": [{"id": "m3", "url": "/exemple", "ancre": "s3"}]}]
        },
    }
    trouves = {"a": [corpus[0], corpus[1]], "b": [corpus[0]], "x": [corpus[0]]}
    d = {"a": 0.2, "b": 0.3, "x": 0.6}
    r = rappel.mesurer_rappel(questions, trouves, d, gold, 6)
    assert (r["questions"], r["rappel_tous_les_faits"], r["manques"]) == (2, 1, ["b"])
    assert r["rang_moyen_reciproque"] == 0.25
    assert r["aire_roc_distance"] == 1.0
    page = Morceau("p", "/exemple", "", "t", "t", "x", "fr", "exemple", "page", "e")
    assert rappel.touche(page, {"url": "/exemple", "ancre": "s9"})


# Banc d'aiguillage.


def test_banc_d_aiguillage():
    banc = aiguillage.charger_banc()
    assert len(banc) == 60
    assert {c: sum(1 for t in banc if t.classe == c) for c in aiguillage.CLASSES} == dict.fromkeys(
        aiguillage.CLASSES, 15
    )
    assert sum(1 for t in banc if t.moitie == "verification") == 30
    v1 = aiguillage.mesurer(banc, "v1")
    classes = aiguillage.mesurer(banc, "classes")
    assert classes["rappel_contact_personnel"] > v1["rappel_contact_personnel"]
    travail = jeu.charger(jeu.JEU_V2_BROUILLON)
    assert aiguillage.faux_positifs_jeu(travail, "classes") == []
    assert aiguillage.verdict(v1, classes, []).startswith("classes remplace")
    assert aiguillage.verdict(v1, classes, ["v49"]).startswith("v1 reste")


def test_le_jeu_v2_porte_ses_groupes():
    travail = jeu.charger(jeu.JEU_V2_BROUILLON)
    groupes = {x.groupe for x in travail if x.groupe.startswith("q")}
    assert groupes == {f"q{i:02d}" for i in range(1, 13)}
    assert all(x.groupe == x.id for x in travail if not x.groupe.startswith("q"))


# assistant-evaluer en processus, hors ligne : --config, --repetitions, empreintes.


def test_evaluer_une_configuration_repetee(tmp_path, monkeypatch, corpus_exemple):
    morceaux, coord = corpus_exemple
    ecrire(morceaux, coord, tmp_path / "corpus.jsonl")
    monkeypatch.setenv("ASSISTANT_CORPUS_FICHIER", str(tmp_path / "corpus.jsonl"))
    monkeypatch.setenv("ASSISTANT_ALTCHA_COUT", "10")
    fichier_jeu = tmp_path / "jeu.yaml"
    fichier_jeu.write_text(
        yaml.safe_dump(
            [
                {
                    "id": "a",
                    "langue": "fr",
                    "famille": "fait_chiffre",
                    "attendu": "repondre",
                    "question": "Quel taux de détection des pannes ?",
                    "pages": ["exemple"],
                    "faits": ["87,3 %"],
                },
                {
                    "id": "b",
                    "langue": "fr",
                    "famille": "personnel",
                    "attendu": "refuser",
                    "question": "Combien il gagne ?",
                },
                {
                    "id": "c",
                    "langue": "en",
                    "famille": "contact",
                    "attendu": "carte",
                    "question": "How can I reach him?",
                },
            ],
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    sortie_eval = tmp_path / "c0.json"
    code = evaluer_main(
        [
            "--fichier-jeu",
            str(fichier_jeu),
            "--config",
            str(RACINE / "evaluation" / "configs" / "10-c0.toml"),
            "--config",
            str(RACINE / "evaluation" / "configs" / "23b-aiguillage-classes.toml"),
            "--repetitions",
            "2",
            "--parallele",
            "2",
            "--sortie",
            str(sortie_eval),
        ]
    )
    assert code == 0
    r1 = json.loads((tmp_path / "c0-r1.json").read_text(encoding="utf-8"))
    assert (tmp_path / "c0-r2.json").exists() and not sortie_eval.exists()
    assert r1["architecture"] == "C" and r1["repetitions"] == 2 and r1["parallele"] == 2
    assert set(r1["empreintes"]) >= {"config", "consigne", "corpus", "corpus_source", "jeu", "juge"}
    assert r1["config"]["effective"]["aiguillage"]["version"] == "classes"
    assert r1["config"]["effective"]["moteur"]["projet_gcp"] == "(masque)"
    assert r1["resultats"]["b"]["chemin"] == "refus_personnel"  # "combien il gagne" : classes
    assert r1["resultats"]["a"]["meilleure_distance"] is not None
    assert "cout_production_usd" in r1["resultats"]["a"]


# Workflow de comparaison.


def test_workflow_de_comparaison():
    chemin = RACINE / ".github" / "workflows" / "comparaison.yml"
    w = yaml.safe_load(chemin.read_text(encoding="utf-8"))
    declencheur = w.get("on") or w.get(True)
    entrees = declencheur["workflow_dispatch"]["inputs"]
    assert {"operation", "configs", "repetitions", "fichier_jeu", "base_run_id"} <= set(entrees)
    texte = chemin.read_text(encoding="utf-8")
    assert "google-github-actions/auth" in texte and "workload_identity_provider" in texte
    assert "upload-artifact" in texte and "GITHUB_STEP_SUMMARY" in texte
    assert "--jeu reserve" not in texte  # la reserve ne se joue jamais depuis ce workflow
    # Chaque commande appelee existe.
    scripts = __import__("tomllib").loads((RACINE / "pyproject.toml").read_text(encoding="utf-8"))
    commandes = set(scripts["project"]["scripts"])
    appelees = {mot for mot in texte.split() if mot.startswith("assistant-")}
    assert appelees <= commandes, appelees - commandes
    for nom in yaml.safe_load(chemin.read_text(encoding="utf-8"))["jobs"]:
        assert nom
    assert Path(RACINE / "evaluation" / "configs" / "00-b0.toml").exists()


# Validation du juge (etape 0.3) : tirage a parts egales, stratifie, kappa.


def test_echantillon_de_notation_et_kappa():
    from assistant.evaluation import noter

    def elements(prefixe):
        return [
            {"id": f"{prefixe}:{f}{i}", "famille": f, "juge": i % 2 == 0}
            for f, n in (("description", 30), ("renseignement", 15), ("injection", 5))
            for i in range(n)
        ]

    tirage = noter.echantillon([elements("b0"), elements("c0")], 40)
    assert len(tirage) == 40
    assert sum(1 for e in tirage if e["id"].startswith("b0:")) == 20
    familles = {e["famille"] for e in tirage if e["id"].startswith("c0:")}
    assert familles == {"description", "renseignement", "injection"}
    assert tirage == noter.echantillon([elements("b0"), elements("c0")], 40)  # graine fixe
    assert noter.kappa_cohen([(True, True), (False, False)] * 5) == 1.0
    assert noter.kappa_cohen([(True, False), (False, True)] * 5) < 0
    juges = [{"id": str(i), "question": "", "juge": i < 9, "motif_juge": ""} for i in range(10)]
    notes = {str(i): i < 8 for i in range(10)}  # 90 % d'accord, mais kappa sous 0,7
    a = noter.accord(juges, notes)
    assert a["accord"]["taux"] == 0.9 and a["kappa"] < 0.7 and not a["fiable"]
