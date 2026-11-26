"""Memoire de conversation : reecriture, sujet courant, renouvellement glissant."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from assistant import config
from assistant.api import creer_app
from assistant.embeddings import FauxEmbedder
from assistant.evaluation import jeu as jeu_mod
from assistant.evaluation.executer import resultat
from assistant.evaluation.jeu import QuestionEval
from assistant.evaluation.juge import contenu_juge
from assistant.fabrique import construire
from assistant.generation import FauxGenerateur
from assistant.memoire import Conversation, Memoire, Sujet, tronquer
from assistant.modeles import Evenement, Morceau, Passage
from assistant.pipeline import Assistant, Parametres, charger_consigne, construire_contenu
from assistant.recherche import RechercheMemoire
from assistant.reecriture import (
    FauxReecrivain,
    ReglagesMemoire,
    ajoute_un_nom,
    contenu_reecriture,
    demande_de_transformation,
    langue_demandee,
    noms,
    noms_du_corpus,
    plan_depuis,
)
from assistant.session import Sessions

from .conftest import ENV_TEST, SITE, ouvrir_session

Q_PANNES = "Quel taux de détection des pannes le moniteur atteint-il ?"
Q_FORMATION = "Quelle formation a-t-il suivie ?"


def suite(q: str) -> dict:
    return {"type": "suite", "question_autonome": q, "sous_questions": []}


@pytest.fixture
def fabriquer(corpus_exemple):
    async def fabriquer(reecrivain=None, generateur=None, seuil=0.95, **memoire):
        morceaux, coord = corpus_exemple
        embedder = FauxEmbedder(256)
        recherche = await RechercheMemoire.construire(morceaux, embedder)
        p = Parametres(seuil_distance=seuil, site=SITE)
        rm = ReglagesMemoire(connus=noms_du_corpus(morceaux), **memoire)
        return Assistant(
            embedder,
            recherche,
            generateur or FauxGenerateur(),
            p,
            coord,
            reecrivain=reecrivain,
            memoire=rm,
            morceaux=morceaux,
        )

    return fabriquer


def conv(tours: int = 3) -> Conversation:
    return Conversation(tours, 300)


async def poser(a: Assistant, q: str, c: Conversation | None, langue: str = "fr"):
    return [e async for e in a.repondre(q, langue, diagnostic=True, conversation=c)]


def fin(evts) -> dict:
    return next(e for e in evts if e.type == "fin").donnees


def diag(evts) -> dict:
    return next(e for e in evts if e.type == "diagnostic").donnees


def sans_temps(evts) -> list:
    """Evenements sans les mesures de temps, pour comparer deux passages."""
    sortie = []
    for e in evts:
        d = json.loads(json.dumps(e.donnees))
        if e.type == "fin":
            for k in ("duree_ms", "premier_mot_ms"):
                d["bilan"].pop(k, None)
        sortie.append((e.type, d))
    return sortie


# --- Premier tour : rien ne change -------------------------------------------


async def test_premier_tour_strictement_inchange(fabriquer):
    r = FauxReecrivain()
    gen_avec, gen_sans = FauxGenerateur(), FauxGenerateur()
    avec = await fabriquer(reecrivain=r, generateur=gen_avec)
    sans = await fabriquer(generateur=gen_sans)
    c = conv()
    e_avec = await poser(avec, Q_PANNES, c)
    e_sans = await poser(sans, Q_PANNES, None)
    assert r.appels == []  # aucun appel de plus
    assert sans_temps(e_avec) == sans_temps(e_sans)
    assert gen_avec.appels == gen_sans.appels
    assert fin(e_avec)["bilan"]["memoire"] == ""
    # L'echange est note, et devient le sujet courant.
    assert c.sujet is not None and c.sujet.question == Q_PANNES
    assert c.echanges[0].passages and not c.echanges[0].refus


async def test_sans_reecrivain_aucune_memoire(fabriquer):
    a = await fabriquer()
    c = conv()
    await poser(a, Q_PANNES, c)
    assert not c.echanges and c.sujet is None


# --- Suite ---------------------------------------------------------------------


async def test_suite_cherche_et_redige_la_question_autonome(fabriquer):
    autonome = "Quelle est la période du projet du moniteur de pannes ?"
    r = FauxReecrivain([suite(autonome)])
    gen = FauxGenerateur()
    a = await fabriquer(reecrivain=r, generateur=gen)
    c = conv()
    await poser(a, Q_PANNES, c)
    evts = await poser(a, "Et sa période ?", c)
    assert len(r.appels) == 1
    assert fin(evts)["bilan"]["memoire"] == "suite"
    assert fin(evts)["bilan"]["appels_modele"] == 2  # reecriture et redaction
    assert gen.appels[-1]["contenu"].startswith(f"Question : {autonome}\n")
    assert diag(evts)["question_traitee"] == autonome
    # La reecriture recoit le sujet courant, l'echange, puis la question, en donnees.
    contenu = r.appels[0]["contenu"]
    assert "<sujet_courant>" in contenu and Q_PANNES in contenu
    assert "<question_actuelle>\nEt sa période ?\n</question_actuelle>" in contenu
    assert c.sujet.question == autonome


async def test_seuil_applique_a_la_question_reecrite(fabriquer):
    r = FauxReecrivain([suite("Donne-moi une recette de crêpes au moniteur.")])
    gen = FauxGenerateur()
    a = await fabriquer(reecrivain=r, generateur=gen, seuil=0.05)
    c = conv()
    c.noter(Q_PANNES, "Il détecte 87,3 % des pannes.", (), False, Sujet(Q_PANNES))
    evts = await poser(a, "Et la recette ?", c)
    assert fin(evts)["chemin"] == "refus_seuil" and gen.appels == []


async def test_la_question_reecrite_repasse_par_l_aiguillage(fabriquer):
    r = FauxReecrivain([suite("Quel salaire le moniteur de pannes rapporte-t-il ?")])
    gen = FauxGenerateur()
    a = await fabriquer(reecrivain=r, generateur=gen)
    c = conv()
    await poser(a, Q_PANNES, c)
    evts = await poser(a, "Et combien ça rapporte ?", c)
    assert fin(evts)["chemin"] == "refus_personnel"
    assert len(gen.appels) == 1  # seulement la premiere question


# --- Nouveau sujet ---------------------------------------------------------------


async def test_nouveau_sujet_garde_la_question_d_origine(fabriquer):
    r = FauxReecrivain(
        [{"type": "nouveau_sujet", "question_autonome": "autre chose", "sous_questions": []}]
    )
    gen = FauxGenerateur()
    a = await fabriquer(reecrivain=r, generateur=gen)
    c = conv()
    await poser(a, Q_PANNES, c)
    evts = await poser(a, Q_FORMATION, c)
    assert fin(evts)["bilan"]["memoire"] == "nouveau_sujet"
    assert gen.appels[-1]["contenu"].startswith(f"Question : {Q_FORMATION}\n")
    assert c.sujet.question == Q_FORMATION  # le nouveau sujet remplace l'ancien


# --- Transformation ---------------------------------------------------------------


async def test_transformation_sans_recherche_passages_recharges(fabriquer):
    demande = "Traduire en anglais la réponse sur le taux de détection des pannes."
    r = FauxReecrivain([{"type": "transformation", "question_autonome": demande}])
    gen = FauxGenerateur()
    a = await fabriquer(reecrivain=r, generateur=gen)
    c = conv()
    premier = await poser(a, Q_PANNES, c)
    sources_avant = next(e for e in premier if e.type == "sources").donnees["sources"]
    vecteurs = a.embedder.appels
    evts = await poser(a, "Traduis en anglais.", c)
    assert a.embedder.appels == vecteurs  # aucune recherche
    assert "etape" in [e.type for e in evts]
    assert all(
        e.donnees["texte"] != "Recherche dans le portfolio" for e in evts if e.type == "etape"
    )
    contenu = gen.appels[-1]["contenu"]
    assert contenu.startswith(f"Question : {demande}\n")
    assert "<reponse_precedente>" in contenu and c.echanges[0].reponse in contenu
    sources = next(e for e in evts if e.type == "sources").donnees["sources"]
    assert [s["url"] for s in sources] == [s["url"] for s in sources_avant]
    assert fin(evts)["bilan"]["memoire"] == "transformation"
    assert c.sujet.question == Q_PANNES  # une transformation ne change pas le sujet


async def test_transformation_apres_un_refus_retombe(fabriquer):
    r = FauxReecrivain([{"type": "transformation", "question_autonome": "Traduire la réponse."}])
    a = await fabriquer(reecrivain=r)
    c = conv()
    c.noter(Q_PANNES, "Il détecte 87,3 % des pannes.", ("id-x",), False, Sujet(Q_PANNES))
    c.noter("Une recette ?", "", (), True)
    evts = await poser(a, "Traduis.", c)
    assert fin(evts)["bilan"]["memoire"] == "repli"


# --- Comparaison ---------------------------------------------------------------


async def test_comparaison_une_recherche_par_sous_question(fabriquer):
    sous = ["Quel taux de détection des pannes le moniteur atteint-il ?", Q_FORMATION]
    r = FauxReecrivain(
        [
            {
                "type": "comparaison",
                "question_autonome": "Comparer le moniteur de pannes et la formation.",
                "sous_questions": sous,
            }
        ]
    )
    gen = FauxGenerateur()
    a = await fabriquer(reecrivain=r, generateur=gen, passages_comparaison=5)
    c = conv()
    await poser(a, Q_PANNES, c)
    evts = await poser(a, "Compare avec sa formation.", c)
    d = diag(evts)
    assert fin(evts)["bilan"]["memoire"] == "comparaison"
    assert {p["projet"] for p in d["passages"]} == {"exemple", "parcours"}
    assert len(d["passages"]) <= 5
    assert len({p["id"] for p in d["passages"]}) == len(d["passages"])


async def test_comparaison_sous_questions_hors_sujet_refusee(fabriquer):
    r = FauxReecrivain(
        [
            {
                "type": "comparaison",
                "question_autonome": "Comparer deux recettes.",
                "sous_questions": ["recette de crêpes", "recette de tarte"],
            }
        ]
    )
    gen = FauxGenerateur()
    a = await fabriquer(reecrivain=r, generateur=gen, seuil=0.05)
    c = conv()
    c.noter(Q_PANNES, "Il détecte 87,3 % des pannes.", (), False, Sujet(Q_PANNES))
    evts = await poser(a, "Compare les recettes.", c)
    assert fin(evts)["chemin"] == "refus_seuil" and gen.appels == []


# --- Garde-fous : nom ajoute, echec, delai ---------------------------------------


async def test_garde_fou_nom_ajoute(fabriquer):
    r = FauxReecrivain([suite("Le moniteur de pannes tourne-t-il sur Kubernetes ?")])
    gen = FauxGenerateur()
    a = await fabriquer(reecrivain=r, generateur=gen)
    c = conv()
    await poser(a, Q_PANNES, c)
    evts = await poser(a, "Et où tourne-t-il ?", c)
    assert fin(evts)["bilan"]["memoire"] == "repli"
    assert gen.appels[-1]["contenu"].startswith("Question : Et où tourne-t-il ?\n")


@pytest.mark.parametrize(
    "reecrivain",
    [
        FauxReecrivain(erreur=RuntimeError("429 quota")),
        FauxReecrivain(erreur=json.JSONDecodeError("illisible", "", 0)),
        FauxReecrivain(attente_s=0.5),
        FauxReecrivain([{"type": "inconnu"}]),
        FauxReecrivain(["pas un objet"]),
        FauxReecrivain([suite("x" * 501)]),
    ],
    ids=["erreur", "json", "delai", "type", "forme", "longueur"],
)
async def test_echec_de_reecriture_question_d_origine_sans_erreur(fabriquer, reecrivain):
    gen = FauxGenerateur()
    a = await fabriquer(reecrivain=reecrivain, generateur=gen, delai_reecriture_s=0.05)
    c = conv()
    c.noter(Q_PANNES, "Il détecte 87,3 % des pannes.", (), False, Sujet(Q_PANNES))
    evts = await poser(a, Q_FORMATION, c)
    assert "erreur" not in [e.type for e in evts]
    assert fin(evts)["bilan"]["memoire"] == "repli"
    assert fin(evts)["chemin"] == "reponse"
    assert gen.appels[-1]["contenu"].startswith(f"Question : {Q_FORMATION}\n")


# --- Historique jamais transmis au redacteur ---------------------------------------


async def test_historique_jamais_transmis_au_redacteur(fabriquer):
    """Le contenu du redacteur est entierement fixe par la question traitee et
    les passages : rien de l'historique ne s'y ajoute, la consigne ne change pas."""
    r = FauxReecrivain()  # regle : une question courte devient une suite
    gen = FauxGenerateur()
    a = await fabriquer(reecrivain=r, generateur=gen)
    c = conv()
    questions = [Q_PANNES, "Et sa période ?", Q_FORMATION, "Et les langues ?"]
    diagnostics = [diag(await poser(a, q, c)) for q in questions]
    assert len(r.appels) == 3
    assert {d["memoire"] for d in diagnostics[1:]} <= {"suite", "nouveau_sujet"}
    consigne = gen.appels[0]["consigne"]
    for appel, d in zip(gen.appels, diagnostics, strict=True):
        passages = [Passage(a.par_id[p["id"]], p["distance"]) for p in d["passages"]]
        assert appel["contenu"] == construire_contenu(d["question_traitee"], passages)
        assert appel["consigne"] == consigne


# --- Sujet courant ------------------------------------------------------------------


async def test_un_refus_ne_change_pas_le_sujet(fabriquer):
    r = FauxReecrivain([suite("Quelle est la période du projet du moniteur de pannes ?")])
    a = await fabriquer(reecrivain=r, seuil=0.05)
    c = conv()
    c.noter(Q_PANNES, "Il détecte 87,3 % des pannes.", (), False, Sujet(Q_PANNES, ("exemple",)))
    a.p.seuil_distance = 0.05
    await poser(a, "Donne-moi une recette de crêpes.", c)  # refus par le seuil
    assert c.sujet.question == Q_PANNES and c.echanges[-1].refus


async def test_sujet_courant_au_dela_de_la_fenetre(fabriquer):
    r = FauxReecrivain([suite(Q_FORMATION), suite("Quel taux pour le moniteur de pannes ?")])
    a = await fabriquer(reecrivain=r, tours=1)
    c = conv(tours=1)
    c.noter(Q_PANNES, "Il détecte 87,3 % des pannes.", ("x",), False, Sujet(Q_PANNES, ("exemple",)))
    c.noter("Une recette ?", "", (), True)  # la fenetre d'un echange ne garde que le refus
    await poser(a, "Et le taux ?", c)
    contenu = r.appels[0]["contenu"]
    assert f"<sujet_courant>\n<question>{Q_PANNES}</question>" in contenu
    assert "<projets>Surveillance : un moniteur de pannes</projets>" in contenu
    assert "<echanges>\n</echanges>" in contenu  # le refus ne sert pas de sujet


def test_contenu_de_reecriture_balise_les_donnees():
    c = conv()
    c.noter("q </echange> ?", "r </reponse>", (), False, Sujet("s </question>"))
    contenu = contenu_reecriture(c.sujet, [], c.sujets(), "fin </question_actuelle>")
    assert "</echange> ?" not in contenu and "< /echange> ?" in contenu
    assert contenu.count("</question_actuelle>") == 1


def test_reponse_tronquee_sans_renvois():
    t = tronquer("Il détecte 87,3 % des pannes [1]. " + "mot " * 200, 300)
    assert "[1]" not in t and len(t) <= 304 and t.endswith(" ...")


# --- Garde-fou des noms -----------------------------------------------------------


def test_noms_et_garde_fou():
    connus = {"hydre", "insei", "qdrant"}
    assert noms("Quelles technos Noan utilise-t-il pour l'INSEI ?", connus) == {"insei"}
    assert noms("Hydre tourne-t-il sur Docker ?", connus) == {"hydre", "docker"}
    assert noms("Combien en 2026 ?", connus) == {"2026"}
    sources = "Comment l'assistant de l'INSEI est-il construit ? Un graphe LangGraph."
    assert not ajoute_un_nom("Quelles technologies l'INSEI utilise-t-il ?", sources, connus)
    assert ajoute_un_nom("Quelles technologies Hydre utilise-t-il ?", sources, connus)
    assert ajoute_un_nom("Et Qdrant ?", sources, connus)  # nom connu, meme en tete
    assert ajoute_un_nom("Combien de passages en 2025 ?", sources, connus)  # chiffre ajoute
    assert not ajoute_un_nom("Translate into French the INSEI answer.", sources, connus)


def test_plan_comparaison_incomplete_devient_suite():
    objet = {"type": "comparaison", "question_autonome": "Comparer A.", "sous_questions": ["a"]}
    plan = plan_depuis(objet, "Compare ?", "Comparer A.", set())
    assert plan is not None and plan.type == "suite" and plan.sous_questions == ()


# --- Memoire en processus : expiration glissante, purge ------------------------------


def test_memoire_expiration_glissante_et_purge():
    t = [1000.0]
    m = Memoire(duree_s=1800, horloge=lambda: t[0])
    c = m.ouvrir("a")
    c.noter("q", "r", (), False, Sujet("q"))
    t[0] += 1700
    assert m.ouvrir("a") is c  # une question repousse l'expiration
    t[0] += 1700
    assert m.active("a")  # 3400 s apres le debut, 1700 s apres la derniere question
    t[0] += 1801
    assert not m.active("a") and len(m) == 0  # purgee
    assert m.ouvrir("a").sujet is None


def test_memoire_taille_bornee(monkeypatch):
    monkeypatch.setattr(Memoire, "TAILLE_MAX", 3)
    m = Memoire()
    for i in range(5):
        m.ouvrir(str(i))
    assert len(m) == 3 and not m.active("0") and m.active("4")


# --- Jeton : identifiant de conversation, renouvellement -----------------------------


def test_jeton_porte_la_conversation():
    s = Sessions(b"k" * 32, 1800)
    j1 = s.emettre(maintenant=1000)
    v1 = s.verifier(j1, maintenant=1500)
    j2 = s.emettre(maintenant=2000, conversation=v1.conversation)
    v2 = s.verifier(j2, maintenant=2500)
    assert v2.conversation == v1.conversation and v2.nonce != v1.nonce
    # Ancien jeton : repris jusqu'a 30 min apres son expiration, signe seulement.
    assert s.conversation_precedente(j1, maintenant=1000 + 1800 + 1799) == v1.conversation
    assert s.conversation_precedente(j1, maintenant=1000 + 1800 + 1801) is None
    corps, sig = j1.split(".")
    assert s.conversation_precedente(corps + "." + "0" * len(sig), maintenant=1500) is None
    assert s.conversation_precedente("n'importe quoi", maintenant=1500) is None


# --- API : renouvellement glissant, evaluation, journaux --------------------------


@pytest.fixture
async def composants(reglages_test, corpus_exemple):
    morceaux, coord = corpus_exemple
    c = await construire(reglages_test, morceaux, coord)
    c.identite_eval = type("Id", (), {"verifier": lambda self, j: j == "jeton-eval-valide"})()
    return c


@pytest.fixture
def client(composants):
    with TestClient(creer_app(composants)) as cl:
        yield cl


def poser_http(client, q: str, jeton: str):
    return client.post(
        "/question",
        json={"question": q, "langue": "fr"},
        headers={"Authorization": f"Bearer {jeton}", "X-Forwarded-For": "198.51.100.9"},
    )


def renouveler(client, precedente: str) -> dict:
    from .conftest import resoudre

    charge = resoudre(client.get("/defi").json())
    r = client.post("/session", json={"altcha": charge, "precedente": precedente})
    assert r.status_code == 200, r.text
    return r.json()


def test_renouvellement_glissant_reprend_la_conversation(client, composants):
    r = FauxReecrivain()
    composants.assistant.reecrivain = r
    j1 = ouvrir_session(client)
    assert poser_http(client, Q_PANNES, j1).status_code == 200
    nouveau = renouveler(client, j1)
    assert nouveau["reprise"] is True
    s1 = composants.sessions.verifier(j1)
    s2 = composants.sessions.verifier(nouveau["session"])
    assert s2.conversation == s1.conversation and s2.nonce != s1.nonce
    poser_http(client, "Et la période ?", nouveau["session"])
    assert len(r.appels) == 1 and Q_PANNES in r.appels[0]["contenu"]


def test_renouvellement_jeton_expire_depuis_moins_de_30_min(client, composants):
    import time

    j1 = ouvrir_session(client)
    poser_http(client, Q_PANNES, j1)
    cid = composants.sessions.verifier(j1).conversation
    # Meme conversation, jeton emis il y a 40 min : expire depuis 10 min.
    vieux = composants.sessions.emettre(maintenant=time.time() - 2400, conversation=cid)
    assert poser_http(client, "Et la période ?", vieux).status_code == 401
    # La memoire a vu une question il y a moins de 30 min : reprise.
    nouveau = renouveler(client, vieux)
    assert nouveau["reprise"] is True
    assert composants.sessions.verifier(nouveau["session"]).conversation == cid
    # Expire depuis plus de 30 min : conversation neuve, meme si elle est active.
    trop_vieux = composants.sessions.emettre(maintenant=time.time() - 3700, conversation=cid)
    assert renouveler(client, trop_vieux)["reprise"] is False


def test_sans_activite_recente_conversation_neuve(client, composants):
    j1 = ouvrir_session(client)
    # Jeton valide mais aucune question : rien a reprendre.
    assert renouveler(client, j1)["reprise"] is False
    poser_http(client, Q_PANNES, j1)
    composants.memoire._conv.clear()  # 30 min sans question, ou redemarrage
    assert renouveler(client, j1)["reprise"] is False
    assert renouveler(client, "faux.jeton")["reprise"] is False
    assert renouveler(client, "")["reprise"] is False


def test_pas_de_limite_de_questions_par_conversation(client, composants):
    composants.limiteur.par_minute = 100
    j = ouvrir_session(client)
    codes = {poser_http(client, f"Le moniteur, question {i} ?", j).status_code for i in range(12)}
    assert codes == {200}
    conv = composants.memoire.ouvrir(composants.sessions.verifier(j).conversation)
    assert len(conv.echanges) == 3  # la fenetre glissante borne le contexte


def test_evaluation_nomme_sa_conversation(client, composants):
    r = FauxReecrivain()
    composants.assistant.reecrivain = r
    h = {"X-Jeton-Evaluation": "jeton-eval-valide", "X-Conversation": "c01-abc"}
    client.post("/question", json={"question": Q_PANNES}, headers=h)
    client.post("/question", json={"question": "Et la période ?"}, headers=h)
    assert len(r.appels) == 1
    # Sans jeton d'evaluation, l'en-tete ne sert a rien.
    j = ouvrir_session(client)
    poser_http(client, Q_PANNES, j)
    client.post(
        "/question",
        json={"question": "Et la période ?"},
        headers={"Authorization": f"Bearer {j}", "X-Conversation": "c01-abc"},
    )
    assert "eval-c01-abc" in composants.memoire._conv
    assert len(r.appels) == 2  # le visiteur a sa propre conversation


def test_rien_de_la_memoire_dans_les_journaux(client, composants, capsys):
    autonome = "Quelle période pour le projet du moniteur de pannes ?"
    composants.assistant.reecrivain = FauxReecrivain([suite(autonome)])
    j = ouvrir_session(client)
    poser_http(client, Q_PANNES, j)
    poser_http(client, "Et sa période ?", j)
    sortie = capsys.readouterr().out
    lignes = [json.loads(x) for x in sortie.splitlines() if x.startswith("{")]
    assert len([x for x in lignes if x["message"] == "question"]) == 2
    for texte in ("détection", "période", "moniteur", "87,3"):
        assert texte not in sortie
    assert all("memoire" not in x and "question_traitee" not in x for x in lignes)


def test_memoire_coupee(tmp_path, corpus_exemple):
    r = config.charger(
        env={
            **ENV_TEST,
            "ASSISTANT_CORPUS_FICHIER": str(tmp_path / "corpus.jsonl"),
            "ASSISTANT_MEMOIRE_ACTIVE": "false",
        }
    )
    assert not r.memoire_active


async def test_memoire_coupee_composants(tmp_path, corpus_exemple):
    morceaux, coord = corpus_exemple
    r = config.charger(
        env={
            **ENV_TEST,
            "ASSISTANT_CORPUS_FICHIER": str(tmp_path / "corpus.jsonl"),
            "ASSISTANT_MEMOIRE_ACTIVE": "false",
        }
    )
    c = await construire(r, morceaux, coord)
    assert c.memoire is None and c.assistant.reecrivain is None
    with TestClient(creer_app(c)) as cl:
        j = ouvrir_session(cl)
        assert renouveler(cl, j)["reprise"] is False


# --- Evaluation multi-tours --------------------------------------------------------


def test_jeu_conversations_charge():
    qs = jeu_mod.charger(jeu_mod.JEU_CONVERSATIONS)
    assert len(qs) == 40 and all(q.tours for q in qs)
    familles: dict[str, int] = {}
    for q in qs:
        familles[q.famille] = familles.get(q.famille, 0) + 1
    assert len(familles) == 10 and min(familles.values()) >= 4
    isolees = jeu_mod.charger(jeu_mod.JEU_TRAVAIL)
    assert not any(q.tours for q in isolees)
    assert all(q.question_jugee == q.question for q in isolees)


def test_regles_de_publication_du_jeu():
    texte = jeu_mod.JEU_CONVERSATIONS.read_text(encoding="utf-8").lower()
    assert chr(0x2013) not in texte and chr(0x2014) not in texte
    hydre = [q for q in jeu_mod.charger(jeu_mod.JEU_CONVERSATIONS) if "hydre" in q.pages]
    for q in hydre:
        for mot in ("position", "décision", "decision", "signal"):
            assert all(mot not in f.lower() for f in q.faits)


def test_le_juge_lit_les_questions_precedentes():
    q = QuestionEval(
        id="c", langue="fr", famille="suite_elliptique", question="Et les technos ?",
        attendu="repondre", reponse_attendue="", pages=("x",), faits=("f",),
        tours=("Parle-moi de l'INSEI.",),
    )  # fmt: skip
    contenu = contenu_juge(
        "{date}|{question}|{attendu}|{faits}|{passages}|{reponse}|{sources}",
        q,
        {"texte": "", "sources": []},
        __import__("datetime").date(2026, 11, 26),
    )
    assert (
        "1) Parle-moi de l'INSEI." in contenu and "Question évaluée : Et les technos ?" in contenu
    )


class CibleNotee:
    def __init__(self) -> None:
        self.appels: list[tuple[str, str | None]] = []

    async def poser(self, question, langue, conversation=None):
        self.appels.append((question, conversation))
        yield Evenement("texte", {"texte": "ok"})
        yield Evenement("fin", {"chemin": "reponse", "bilan": {}})


async def test_runner_joue_les_tours_dans_la_meme_conversation():
    q = QuestionEval(
        id="c99", langue="fr", famille="suite_elliptique", question="Et ensuite ?",
        attendu="repondre", reponse_attendue="", pages=("x",), faits=("f",),
        tours=("Premier ?", "Deuxième ?"),
    )  # fmt: skip
    cible = CibleNotee()
    res = await resultat(cible, q)
    assert [a[0] for a in cible.appels] == ["Premier ?", "Deuxième ?", "Et ensuite ?"]
    ids = {a[1] for a in cible.appels}
    assert len(ids) == 1 and next(iter(ids)).startswith("c99-")
    assert [t["question"] for t in res["tours"]] == ["Premier ?", "Deuxième ?"]
    # Un nouvel essai ouvre une conversation neuve.
    await resultat(cible, q)
    assert len({a[1] for a in cible.appels}) == 2


async def test_question_isolee_sans_conversation():
    q = QuestionEval(
        id="t1", langue="fr", famille="description", question="Seule ?",
        attendu="repondre", reponse_attendue="", pages=("x",), faits=("f",),
    )  # fmt: skip
    cible = CibleNotee()
    await resultat(cible, q)
    assert cible.appels == [("Seule ?", None)]


def test_evenement_conversation_neuve(client, composants):
    j = ouvrir_session(client)

    def types(r):
        return [b.split("\n")[0].removeprefix("event: ") for b in r.text.strip().split("\n\n")]

    assert types(poser_http(client, Q_PANNES, j))[0] == "conversation"
    assert "conversation" not in types(poser_http(client, "Et la période ?", j))
    composants.memoire._conv.clear()  # instance arretee faute de trafic : memoire perdue
    assert types(poser_http(client, "Et la période ?", j))[0] == "conversation"


# --- Corrections du 26/11/2026 (mesure Vertex de la memoire) ------------------------

Q_PANNES_EN = "What share of failures does the monitor catch?"


def morceau(contexte: str, texte: str, projet: str = "p", titre: str = "T") -> Morceau:
    return Morceau("i", "/x", "", titre, contexte, texte, "fr", projet, "prose", "e")


def test_noms_du_corpus_sans_mots_courants():
    """Cause des replis c06, c07, c08 et c28 : un mot courant pris dans un titre
    (« En bref », « Head of », « FORMATION ») passait pour un nom connu, et toute
    reecriture qui l'employait retombait sur la question d'origine."""
    morceaux = [
        morceau("Leyenda > En bref", "Le projet Leyenda utilise TensorFlow, en local.", "leyenda"),
        morceau("Parcours > Langues", "Il parle trois langues. Head of data sur le projet."),
        morceau("CV", "Claude Code n8n FORMATION ingénieur. Une formation à l'INSEI."),
        morceau("Background", "Two projects of the school, with Qdrant."),
    ]
    connus = noms_du_corpus(morceaux)
    assert {"leyenda", "tensorflow", "insei", "qdrant"} <= connus
    for courant in ("en", "of", "projet", "langues", "head", "formation", "two", "with"):
        assert courant not in connus
    # La reecriture en francais d'une question anglaise ne bute plus sur ces mots.
    sources = "Which languages does Noan speak? Noan speaks French."
    assert not ajoute_un_nom("Traduis en français la réponse sur les langues.", sources, connus)
    assert not ajoute_un_nom(
        "What was the team size and period of the employee project?",
        "the employee project",
        connus,
    )


@pytest.mark.parametrize(
    "question",
    ["Traduis en Français.", "Say it in English, please.", "Réponds en Anglais ou en Espagnol."],
)
def test_noms_de_langues_jamais_un_nom(question):
    connus = {"francais", "anglais", "espagnol", "english", "french", "spanish"}
    assert noms(question, connus) == set()
    assert not ajoute_un_nom(question, "rien", connus)


@pytest.mark.parametrize(
    ("question", "attendu", "langue"),
    [
        ("Can you say that in French?", True, "fr"),
        ("Translate your answer into English, please.", True, "en"),
        ("Traduis en français.", True, "fr"),
        ("Traduis ta réponse en anglais.", True, "en"),
        ("Et en anglais ?", True, "en"),
        ("Résume ça en une phrase.", True, ""),
        ("Plus court, s'il te plaît.", True, ""),
        ("Shorter, please.", True, ""),
        ("Rephrase that without jargon.", True, ""),
        ("Reformule autrement.", True, ""),
        ("Explain it more simply, for a non-technical recruiter.", True, ""),
        ("Dis-le autrement, pour quelqu'un qui n'est pas technique.", True, ""),
        ("Résume le projet Hydre.", False, ""),  # un nom : nouveau sujet possible
        ("Quelles langues parle-t-il en anglais ?", False, "en"),  # demande un fait
        ("Where can I download his resume?", False, ""),  # resume = CV
        ("Compare those two projects: team size and period.", False, ""),
        ("Et quelles technos utilise-t-il ?", False, ""),
    ],
)
def test_filet_des_transformations(question, attendu, langue):
    assert demande_de_transformation(question, {"hydre"}) is attendu
    assert langue_demandee(question) == langue


def transformation(q: str) -> dict:
    return {"type": "transformation", "question_autonome": q, "sous_questions": []}


@pytest.mark.parametrize(
    ("reecrivain", "raison"),
    [
        # Ecrite dans la langue demandee : plus aucun mot courant ne bloque (c06).
        (
            FauxReecrivain([transformation("Dis en français quelle part des pannes il détecte.")]),
            "",
        ),
        # Garde-fou sur une transformation : la demande d'origine reste une transformation.
        (
            FauxReecrivain([transformation("Translate the Kubernetes answer into French.")]),
            "garde_nom:kubernetes",
        ),
        # Le modele hesite ou ne repond pas : le filet deterministe la rattrape.
        (
            FauxReecrivain([{"type": "nouveau_sujet", "question_autonome": "x"}]),
            "filet:nouveau_sujet",
        ),
        (
            FauxReecrivain([suite("Can you say in French what Kubernetes does?")]),
            "filet:garde_nom:kubernetes",
        ),
        (FauxReecrivain(erreur=RuntimeError("429")), "filet:erreur:RuntimeError"),
        (FauxReecrivain(attente_s=0.5), "filet:delai"),
    ],
    ids=["langue_demandee", "garde", "nouveau_sujet", "suite_bloquee", "erreur", "delai"],
)
async def test_traduction_toujours_une_transformation(fabriquer, reecrivain, raison):
    gen = FauxGenerateur()
    a = await fabriquer(reecrivain=reecrivain, generateur=gen, delai_reecriture_s=0.05)
    c = conv()
    await poser(a, Q_PANNES_EN, c, "en")
    vecteurs = a.embedder.appels
    evts = await poser(a, "Can you say that in French?", c, "en")
    assert fin(evts)["bilan"]["memoire"] == "transformation"
    assert fin(evts)["chemin"] == "reponse"
    assert a.embedder.appels == vecteurs  # aucune recherche, aucun refus par le seuil
    assert diag(evts)["memoire_raison"] == raison
    contenu = gen.appels[-1]["contenu"]
    assert "<reponse_precedente>" in contenu
    # La langue demandee l'emporte sur celle de la question et de la page.
    assert "Langue demandée : français." in contenu


async def test_traduction_vers_l_anglais_depuis_une_page_francaise(fabriquer):
    gen = FauxGenerateur()
    r = FauxReecrivain([transformation("Translate into English the answer on failures.")])
    a = await fabriquer(reecrivain=r, generateur=gen)
    c = conv()
    await poser(a, Q_PANNES, c)
    evts = await poser(a, "Translate your answer into English, please.", c)
    assert fin(evts)["bilan"]["memoire"] == "transformation"
    assert "Langue demandée : anglais." in gen.appels[-1]["contenu"]


def test_regle_de_langue_et_regle_4_de_la_consigne():
    consigne = charger_consigne()
    assert "la demande explicite l'emporte sur la langue de la question" in consigne
    # Comparer deux projets de Noan est permis ; seule la comparaison a une autre
    # personne est refusee (c26 recevait [REFUS]).
    assert "Comparer deux projets, deux expériences ou deux outils de Noan est permis" in consigne
    assert "si elle compare Noan à une autre personne" in consigne
    assert "comparaison avec une autre personne" not in consigne


async def test_ces_deux_projets_resolus_par_les_echanges(fabriquer):
    """c28 : « those two projects » apres deux echanges sur deux sujets. La
    reecriture recoit les projets cites par chaque echange, et un nom qui n'y
    figure que par son titre ne fait plus retomber sur la question d'origine."""
    sous = [
        "Quelle est la période du projet Surveillance ?",
        "Quelle est la période de la formation ?",
    ]
    vus: list[str] = []

    def reponses(contenu: str) -> dict:
        vus.append(contenu)
        if len(vus) == 1:
            return {"type": "nouveau_sujet", "question_autonome": "", "sous_questions": []}
        return {
            "type": "comparaison",
            "question_autonome": "Compare le projet Surveillance et la formation : période.",
            "sous_questions": sous,
        }

    gen = FauxGenerateur()
    a = await fabriquer(reecrivain=FauxReecrivain(reponses), generateur=gen)
    c = conv()
    await poser(a, Q_PANNES, c)
    await poser(a, Q_FORMATION, c)
    evts = await poser(a, "Compare ces deux sujets : la période.", c)
    contenu = vus[-1]
    assert contenu.count("<echange>") == 2
    assert "<projets>Surveillance : un moniteur de pannes</projets>" in contenu
    assert "<projets>Parcours</projets>" in contenu
    assert fin(evts)["bilan"]["memoire"] == "comparaison"
    assert diag(evts)["memoire_raison"] == ""
    # Les passages des deux sous-questions arrivent au redacteur (c26).
    assert {p["projet"] for p in diag(evts)["passages"]} == {"exemple", "parcours"}
    redige = gen.appels[-1]["contenu"]
    assert "moniteur" in redige and "Formation" in redige


@pytest.mark.parametrize(
    ("reecrivain", "raison"),
    [
        (FauxReecrivain(erreur=RuntimeError("429 quota")), "erreur:RuntimeError"),
        (FauxReecrivain(erreur=json.JSONDecodeError("illisible", "", 0)), "json"),
        (FauxReecrivain(attente_s=0.5), "delai"),
        (FauxReecrivain([{"type": "inconnu"}]), "type"),
        (FauxReecrivain(["pas un objet"]), "forme"),
        (FauxReecrivain([suite("")]), "vide"),
        (FauxReecrivain([suite("x" * 501)]), "longueur"),
        (FauxReecrivain([suite("Quelle formation sur Kubernetes ?")]), "garde_nom:kubernetes"),
    ],
    ids=["erreur", "json", "delai", "type", "forme", "vide", "longueur", "garde"],
)
async def test_raison_du_repli_dans_le_diagnostic_seulement(fabriquer, reecrivain, raison):
    a = await fabriquer(reecrivain=reecrivain, delai_reecriture_s=0.05)
    c = conv()
    c.noter(Q_PANNES, "Il détecte 87,3 % des pannes.", (), False, Sujet(Q_PANNES))
    evts = await poser(a, Q_FORMATION, c)
    assert fin(evts)["bilan"]["memoire"] == "repli"
    assert diag(evts)["memoire_raison"] == raison
    assert raison not in json.dumps(fin(evts))  # le bilan part dans les journaux


async def test_raison_transformation_apres_un_refus(fabriquer):
    a = await fabriquer(reecrivain=FauxReecrivain([transformation("Traduire la réponse.")]))
    c = conv()
    c.noter(Q_PANNES, "Il détecte 87,3 % des pannes.", ("id-x",), False, Sujet(Q_PANNES))
    c.noter("Une recette ?", "", (), True)
    evts = await poser(a, "Traduis.", c)
    assert diag(evts)["memoire_raison"] == "transformation_apres_refus"


def test_raison_du_repli_jamais_dans_les_journaux(client, composants, capsys):
    composants.assistant.reecrivain = FauxReecrivain([suite("Le moniteur sur Kubernetes ?")])
    j = ouvrir_session(client)
    poser_http(client, Q_PANNES, j)
    poser_http(client, "Et où tourne-t-il ?", j)
    sortie = capsys.readouterr().out
    assert "garde_nom" not in sortie and "memoire_raison" not in sortie
    assert "kubernetes" not in sortie.lower()


class CibleDiagnostic:
    async def poser(self, question, langue, conversation=None):
        yield Evenement("texte", {"texte": "ok"})
        yield Evenement(
            "diagnostic",
            {"passages": [], "question_traitee": question, "memoire_raison": "delai"},
        )
        yield Evenement("fin", {"chemin": "reponse", "bilan": {"memoire": "repli"}})


async def test_executer_garde_la_raison_du_repli():
    q = QuestionEval(
        id="c98", langue="fr", famille="suite_elliptique", question="Et ensuite ?",
        attendu="repondre", reponse_attendue="", pages=("x",), faits=("f",),
        tours=("Premier ?",),
    )  # fmt: skip
    res = await resultat(CibleDiagnostic(), q)
    assert res["memoire_raison"] == "delai" and res["tours"][0]["memoire_raison"] == "delai"


def test_faits_du_jeu_multitours_sont_des_phrases():
    """Un fait non cite qui contient « : » devenait un dictionnaire YAML."""
    import yaml

    brut = yaml.safe_load(jeu_mod.JEU_CONVERSATIONS.read_text(encoding="utf-8"))
    assert all(isinstance(f, str) for q in brut for f in q.get("faits") or [])
