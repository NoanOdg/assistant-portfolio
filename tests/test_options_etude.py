"""Options de l'etude du 24/11/2026 (partie c) : chaque option se choisit par
configuration, et son defaut reproduit le service tel qu'il etait servi."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from assistant import config
from assistant.corpus.decoupage import decouper
from assistant.corpus.servi import (
    CoordonneeTrouvee,
    compacter,
    controler_coordonnees,
    empreinte_corpus,
    morceaux_servis,
)
from assistant.embeddings import FauxEmbedder
from assistant.etiquette import SCHEMA, LecteurEtiquette
from assistant.fabrique import construire, parametres
from assistant.filtres import chiffres_non_attribues, citations_valides
from assistant.garde import aiguiller, distance_frappe
from assistant.generation import FauxGenerateur, Fragment
from assistant.modeles import Bilan, Morceau, Page, Passage, Section, empreinte
from assistant.pipeline import (
    CONSIGNE_DEFAUT,
    Assistant,
    Parametres,
    charger_consigne,
    construire_contenu,
    couts,
    empreinte_consigne,
)
from assistant.recherche import (
    BM25,
    DISTANCE_INCONNUE,
    RechercheHybride,
    RechercheMemoire,
    RechercheParente,
    fusion_rrf,
)

from .conftest import ENV_TEST, SITE, evenements


def morceau(i: int, texte: str, langue: str = "fr", url: str = "/p", ancre: str = "", **kw):
    ancre = ancre or f"s{i}"
    return Morceau(
        id=f"m{i:03d}",
        url=url,
        ancre=ancre,
        titre=kw.get("titre", "Page"),
        contexte=f"Page > Section {i}",
        texte=texte,
        langue=langue,
        projet=kw.get("projet", "exemple"),
        type=kw.get("type", "prose"),
        empreinte=empreinte(texte),
    )


def types(evts) -> list[str]:
    return [e.type for e in evts]


def fin(evts) -> dict:
    return next(e for e in evts if e.type == "fin").donnees


def texte(evts) -> str:
    return "".join(e.donnees["texte"] for e in evts if e.type == "texte")


# Configuration : superposition, defauts, controles de demarrage.


def test_les_defauts_reproduisent_le_service_servi(reglages_test):
    r = reglages_test
    assert r.mode_recherche == "rag" and r.architecture == "B"
    assert r.moteur["consigne"] == "v1"
    assert r.moteur["ordre_contenu"] == "question_d_abord"
    assert r.moteur["date_du_jour"] is False
    # Seul changement voulu (24/11/2026) : l'aiguillage par classes, qui corrige
    # trois familles d'erreurs des garde-fous du code.
    assert r.aiguillage == {"version": "classes", "etiquette_modele": False}
    assert r.refus["prefiltre_seuil"] is False
    assert r.recherche["hybride"] is False
    assert r.corpus["decoupage"] == "mots360"
    assert r.corpus["inclure_contact"] is False and r.corpus["compact"] is False
    assert r.moteur["tache_question"] == "RETRIEVAL_QUERY" and not r.moteur["titre_document"]
    # La consigne servie ne change pas : meme texte, meme empreinte.
    assert charger_consigne("v1") == CONSIGNE_DEFAUT.read_text(encoding="utf-8")
    p = parametres(r)
    assert (p.tarif_entree, p.tarif_sortie, p.tarif_cache) == (0.25, 1.50, 0.025)
    r.verifier()


def test_superposition_puis_environnement(tmp_path):
    f = tmp_path / "variante.toml"
    f.write_text(
        '[recherche]\nmode = "tout_le_corpus"\n[moteur]\nordre_contenu = "passages_d_abord"\n'
    )
    r = config.charger(env={**ENV_TEST}, superpositions=[f])
    assert r.architecture == "C" and r.superpositions == (str(f),)
    # L'environnement passe au-dessus du fichier superpose.
    r2 = config.charger(env={**ENV_TEST, "ASSISTANT_RECHERCHE_MODE": "rag"}, superpositions=[f])
    assert r2.architecture == "B"
    # Variable d'environnement de superpositions, comme pour Cloud Run.
    r3 = config.charger(env={**ENV_TEST, "ASSISTANT_SUPERPOSITIONS": str(f)})
    assert r3.architecture == "C"
    assert r.empreinte != r2.empreinte


def test_une_cle_inconnue_est_refusee_sauf_un_modele_de_plus(tmp_path):
    faute = tmp_path / "faute.toml"
    faute.write_text("[recherche]\nhybrid = true\n")
    with pytest.raises(KeyError, match=r"recherche\.hybrid"):
        config.charger(env={}, superpositions=[faute])
    modele = tmp_path / "modele.toml"
    modele.write_text(
        '[modeles."gemini-9-essai"]\nentree = 1.0\nsortie = 2.0\ncache = 0.1\n'
        '[moteur]\nmodele_redaction = "gemini-9-essai"\nreflexion = ""\n'
    )
    r = config.charger(env={**ENV_TEST}, superpositions=[modele])
    assert r.tarif("gemini-9-essai") == {"entree": 1.0, "sortie": 2.0, "cache": 0.1}
    r.verifier()


def test_les_configurations_d_experience_se_chargent_et_s_empilent():
    dossier = config.RACINE / "evaluation" / "configs"
    fichiers = sorted(dossier.glob("*.toml"))
    assert len(fichiers) >= 10
    for f in fichiers:
        r = config.charger(env={**ENV_TEST}, superpositions=[dossier / "10-c0.toml", f])
        if f.name.startswith(("11-", "31-", "35-")):
            continue  # agent ou hybride : pas superposables a C
        r.verifier()
    pile = [dossier / n for n in ("10-c0.toml", "21-contact.toml", "22-consigne-v2.toml")]
    r = config.charger(env={**ENV_TEST}, superpositions=pile)
    assert r.architecture == "C" and r.corpus["inclure_contact"] and r.moteur["consigne"] == "v2"


@pytest.mark.parametrize(
    ("variables", "message"),
    [
        ({"ASSISTANT_RECHERCHE_MODE": "tout_le_corpus"}, "passages_d_abord"),
        (
            {
                "ASSISTANT_MOTEUR_REFLEXION": "minimal",
                "ASSISTANT_MOTEUR_MODELE_REDACTION": "gemini-3.8-flash",
            },
            "refusee",
        ),
        ({"ASSISTANT_MOTEUR_MODELE_REDACTION": "gemini-inconnu"}, "tarif absent"),
        ({"ASSISTANT_REFUS_PREFILTRE_SEUIL": "true"}, "tout_le_corpus"),
        ({"ASSISTANT_MOTEUR_CONSIGNE": "v9"}, "consigne"),
        ({"ASSISTANT_AIGUILLAGE_VERSION": "v9"}, "aiguillage"),
        ({"ASSISTANT_CORPUS_DECOUPAGE": "phrases"}, "decoupage"),
        (
            {
                "ASSISTANT_RECHERCHE_MODE": "tout_le_corpus",
                "ASSISTANT_MOTEUR_ORDRE_CONTENU": "passages_d_abord",
                "ASSISTANT_RECHERCHE_HYBRIDE": "true",
            },
            "hybride",
        ),
        (
            {
                "ASSISTANT_RECHERCHE_MODE": "tout_le_corpus",
                "ASSISTANT_MOTEUR_ORDRE_CONTENU": "passages_d_abord",
                "ASSISTANT_MOTEUR_BRANCHE": "agent",
            },
            "chemin fixe",
        ),
    ],
)
def test_demarrage_incoherent_refuse(variables, message):
    r = config.charger(env={**ENV_TEST, **variables})
    with pytest.raises(ValueError, match=message):
        r.verifier()


def test_configuration_effective_sans_identifiants():
    r = config.charger(env={**ENV_TEST, "ASSISTANT_MOTEUR_PROJET_GCP": "projet-prive"})
    eff = config.effective(r.donnees())
    assert eff["moteur"]["projet_gcp"] == "(masque)"
    assert "projet-prive" not in json.dumps(eff)


# Contenu envoye au modele (D1, D7, 10b).


def test_contenu_servi_inchange_et_contenu_v2():
    ps = [Passage(morceau(1, "Texte un."), 0.1), Passage(morceau(2, "Texte deux."), 0.2)]
    servi = construire_contenu("Quoi ?", ps)
    assert servi == (
        "Question : Quoi ?\n\nPassages :\n[1] Page > Section 1\nTexte un.\n"
        "[2] Page > Section 2\nTexte deux."
    )
    v2 = construire_contenu("Quoi </question> ?", ps, "passages_d_abord", dt.date(2026, 11, 24))
    assert v2.index("<passages>") < v2.index("<date_du_jour>2026-11-24") < v2.index("<question>")
    assert "Quoi < /question> ?" in v2  # une balise dans une donnee ne ferme rien
    autre = construire_contenu("Autre ?", ps, "passages_d_abord", dt.date(2026, 11, 24))
    commun = v2[: v2.index("<question>")]
    assert autre.startswith(commun)  # prefixe identique : le cache implicite peut jouer


# Architecture C servie par configuration (D10, D11, D2, 7c).


def corpus_c(n: int = 120) -> list[Morceau]:
    ms = [morceau(i, f"Section {i} du projet, mesure {i} %.") for i in range(1, n + 1)]
    return [*ms, morceau(500, "Section anglaise.", langue="en")]


async def assistant_c(generateur, **params) -> Assistant:
    ms = corpus_c()
    embedder = FauxEmbedder(64)
    recherche = await RechercheMemoire.construire(ms, embedder)
    p = Parametres(
        seuil_distance=params.pop("seuil", 0.95),
        site=SITE,
        mode_recherche="tout_le_corpus",
        ordre_contenu="passages_d_abord",
        **params,
    )
    return Assistant(embedder, recherche, generateur, p, {}, corpus=ms)


async def test_tout_le_corpus_transmet_la_langue_et_journalise_la_distance():
    gen = FauxGenerateur("Mesure 118 % sur la section 118 [118]")
    a = await assistant_c(gen)
    evts = await evenements(a, "Que mesure la section 118 ?", diagnostic=True)
    contenu = gen.appels[0]["contenu"]
    assert "[120] Page > Section 120" in contenu and "Section anglaise" not in contenu
    bilan = fin(evts)["bilan"]
    assert bilan["passages"] == 120 and bilan["meilleure_distance"] is not None
    sources = next(e for e in evts if e.type == "sources").donnees
    assert sources["citees"] and sources["sources"][0]["n"] == 118  # renvoi au-dela de [99]
    assert bilan["chiffres_non_attribues"] == 0
    diag = next(e for e in evts if e.type == "diagnostic").donnees
    assert diag["cites"] == [118] and diag["architecture"] == "C"


async def test_prefiltre_de_seuil_en_tout_le_corpus():
    gen = FauxGenerateur()
    a = await assistant_c(gen, prefiltre_seuil=True, seuil=0.01)
    evts = await evenements(a, "Une recette de crêpes ?")
    assert fin(evts)["chemin"] == "refus_seuil" and gen.appels == []
    sans = await assistant_c(FauxGenerateur(), prefiltre_seuil=False, seuil=0.01)
    assert fin(await evenements(sans, "Une recette de crêpes ?"))["chemin"] != "refus_seuil"


async def test_tout_le_corpus_exige_le_corpus():
    with pytest.raises(ValueError, match="corpus"):
        Assistant(
            FauxEmbedder(8),
            RechercheMemoire(),
            FauxGenerateur(),
            Parametres(seuil_distance=0.5, mode_recherche="tout_le_corpus"),
            {},
        )


async def test_fabrique_sert_c_et_la_consigne_v2(corpus_exemple):
    r = config.charger(
        env={
            **ENV_TEST,
            "ASSISTANT_RECHERCHE_MODE": "tout_le_corpus",
            "ASSISTANT_MOTEUR_ORDRE_CONTENU": "passages_d_abord",
            "ASSISTANT_MOTEUR_CONSIGNE": "v2",
            "ASSISTANT_MOTEUR_DATE_DU_JOUR": "true",
            "ASSISTANT_AIGUILLAGE_ETIQUETTE_MODELE": "true",
        }
    )
    morceaux, coord = corpus_exemple
    c = await construire(r, morceaux, coord)
    a = c.assistant
    assert a.p.mode_recherche == "tout_le_corpus" and a.corpus == morceaux
    assert a.p.date_du_jour and a.p.etiquette_modele
    assert "troisième personne" in a.consigne and "<!--" not in a.consigne
    assert '"intention"' in a.consigne  # bloc de l'etiquette ajoute
    assert a.version_consigne != empreinte_consigne(CONSIGNE_DEFAUT.read_text(encoding="utf-8"))
    assert c.empreinte_corpus == empreinte_corpus(morceaux)
    evts = await evenements(a, "Quel taux de détection des pannes ?")
    assert fin(evts)["bilan"]["meilleure_distance"] is not None


def test_renvois_a_trois_chiffres():
    assert citations_valides("Voir [7] et [150] puis [999]", 178) == [7, 150]


# Cout : cache et reflexion (D4).


def test_cout_compte_le_cache_et_la_reflexion():
    b = Bilan(jetons_entree=36_000, jetons_cache=32_000, jetons_sortie=200, jetons_reflexion=100)
    reel, production = couts(b, 0.25, 1.50, 0.025)
    assert reel == pytest.approx((4_000 * 0.25 + 32_000 * 0.025 + 300 * 1.50) / 1e6)
    assert production == pytest.approx((36_000 * 0.25 + 300 * 1.50) / 1e6)
    assert production > reel


async def test_le_bilan_porte_les_deux_couts(fabriquer_assistant):
    gen = FauxGenerateur(jetons_cache=10, jetons_reflexion=5)
    a = await fabriquer_assistant(generateur=gen)
    b = fin(await evenements(a, "Quel taux de détection des pannes ?"))["bilan"]
    assert b["jetons_cache"] == 10 and b["jetons_reflexion"] == 5
    assert b["cout_production_usd"] > b["cout_usd"] > 0


def test_chiffre_attribue_au_passage_cite():
    assert chiffres_non_attribues("Il atteint 87,3 % [1]", ["Le taux est de 87,3 %"]) == 0
    assert chiffres_non_attribues("Il atteint 92 % [1]", ["Le taux est de 87,3 %"]) == 1


async def test_date_du_jour_donnee_au_modele(fabriquer_assistant):
    gen = FauxGenerateur()
    a = await fabriquer_assistant(
        generateur=gen,
        ordre_contenu="passages_d_abord",
        date_du_jour=True,
        aujourd_hui=lambda: dt.date(2026, 11, 24),
    )
    await evenements(a, "Quel taux de détection des pannes ?")
    assert "<date_du_jour>2026-11-24</date_du_jour>" in gen.appels[0]["contenu"]


# Aiguillage par classes (6b).


@pytest.mark.parametrize(
    ("question", "v1", "classes"),
    [
        ("Comment le contcter ?", "recherche", "contact"),
        ("Son linkdin ?", "recherche", "contact"),
        ("Quel est son numéro de téléphone ?", "personnel", "telephone"),
        ("Combien il gagne ?", "recherche", "personnel"),
        ("How much does he make?", "recherche", "personnel"),
        ("Combien de projets il a gagnés ?", "recherche", "recherche"),
        ("What contract is he looking for?", "recherche", "recherche"),
        ("What exactly was the robotic cell thing about?", "recherche", "recherche"),
        ("Fais-moi un résumé de son parcours", "recherche", "recherche"),
        ("Quel salaire demande-t-il ?", "personnel", "personnel"),
        ("Comment le contacter ?", "contact", "contact"),
    ],
)
def test_aiguillage_v1_et_classes(question, v1, classes):
    assert aiguiller(question) == v1  # defaut : v1, le service servi
    assert aiguiller(question, "classes") == classes


def test_distance_de_frappe():
    assert distance_frappe("contact", "conatct") == 1  # inversion
    assert distance_frappe("contact", "contcat") == 1
    assert distance_frappe("contact", "contrat") == 2


async def test_telephone_vers_la_carte_avec_son_texte(fabriquer_assistant):
    a = await fabriquer_assistant(aiguillage="classes")
    evts = await evenements(a, "Quel est son numéro de téléphone ?")
    assert types(evts)[0] == "carte" and fin(evts)["chemin"] == "carte"
    assert "Aucun numéro de téléphone n'est publié" in texte(evts)
    assert fin(evts)["bilan"]["aiguillage"] == "code"


# Etiquette du modele en sortie structuree (6c).


def test_lecteur_d_etiquette_en_fragments():
    brut = json.dumps(
        {"intention": "question", "reponse": 'Il a dit "oui".\nDétecté à 87 % [1]'},
        ensure_ascii=True,
    )
    lecteur = LecteurEtiquette()
    sortie = "".join(lecteur.pousser(brut[i : i + 3]) for i in range(0, len(brut), 3))
    sortie += lecteur.terminer()
    assert lecteur.intention == "question"
    assert sortie == 'Il a dit "oui".\nDétecté à 87 % [1]'


def test_lecteur_d_etiquette_retient_le_texte_avant_l_intention():
    lecteur = LecteurEtiquette()
    assert lecteur.pousser('{"reponse": "Bonjour", ') == ""
    assert lecteur.pousser('"intention": "question"}') == "Bonjour"
    illisible = LecteurEtiquette()
    illisible.pousser("pas du json")
    illisible.terminer()
    assert illisible.intention == "inconnue"


@pytest.mark.parametrize(
    ("intention", "chemin", "attendu"),
    [
        ("contact", "carte", "carte"),
        ("personnel", "refus_personnel", "refus"),
        ("hors_sujet", "refus_modele", "refus"),
    ],
)
async def test_etiquette_rendue_par_le_code(fabriquer_assistant, intention, chemin, attendu):
    gen = FauxGenerateur(json.dumps({"intention": intention, "reponse": "Texte du modele"}))
    a = await fabriquer_assistant(generateur=gen, etiquette_modele=True)
    evts = await evenements(a, "Le moniteur détecte-t-il les pannes ?")
    assert gen.appels[0]["schema"] == SCHEMA
    assert fin(evts)["chemin"] == chemin and attendu in types(evts)
    assert "Texte du modele" not in texte(evts)  # le modele n'ecrit ni carte ni refus
    b = fin(evts)["bilan"]
    assert b["aiguillage"] == "modele" and b["intention"] == intention


async def test_etiquette_question_part_en_flux(fabriquer_assistant):
    gen = FauxGenerateur()  # faux en sortie structuree : intention "question"
    a = await fabriquer_assistant(generateur=gen, etiquette_modele=True)
    evts = await evenements(a, "Quel taux de détection des pannes le moniteur atteint-il ?")
    assert fin(evts)["chemin"] == "reponse" and "[1]" in texte(evts)
    assert "intention" not in texte(evts)
    assert gen.appels[0]["schema"] is not None


async def test_sans_etiquette_aucun_schema(fabriquer_assistant):
    gen = FauxGenerateur()
    a = await fabriquer_assistant(generateur=gen)
    await evenements(a, "Quel taux de détection des pannes ?")
    assert gen.appels[0]["schema"] is None


async def test_les_mots_cles_passent_avant_le_modele(fabriquer_assistant):
    gen = FauxGenerateur()
    a = await fabriquer_assistant(generateur=gen, etiquette_modele=True)
    evts = await evenements(a, "Quel salaire demande-t-il ?")
    assert fin(evts)["chemin"] == "refus_personnel" and gen.appels == []


# Recherche : hybride, page parente ; decoupage.


def test_bm25_et_fusion_rrf():
    ms = [morceau(1, "Kubernetes et Helm"), morceau(2, "Recette de crêpes"), morceau(3, "Helm")]
    scores = BM25(ms).scores("helm kubernetes")
    assert scores[0] > scores[2] > scores[1] == 0
    assert fusion_rrf([["a", "b"], ["b", "c"]], 60) == ["b", "a", "c"]


class RechercheFixe:
    def __init__(self, passages: list[Passage]) -> None:
        self.passages = passages

    async def chercher(self, vecteur, langue, k, projet=None, texte=None):
        return self.passages[:k]


async def test_recherche_hybride_garde_la_distance_vectorielle():
    ms = [morceau(i, t) for i, t in enumerate(["moniteur de pannes", "zxqv rare", "autre"], 1)]
    h = RechercheHybride(RechercheFixe([Passage(ms[0], 0.3)]), ms, candidats=5)
    ps = await h.chercher([0.0], "fr", 2, texte="zxqv")
    assert [(p.morceau.id, p.distance) for p in ps] == [
        ("m001", 0.3),
        ("m002", DISTANCE_INCONNUE),  # trouve par BM25 seul : distance inconnue
    ]
    assert min(p.distance for p in ps) == 0.3  # le seuil porte sur le meilleur vectoriel
    assert [p.morceau.id for p in await h.chercher([0.0], "fr", 2)] == ["m001"]  # sans texte


async def test_page_parente():
    ms = [
        morceau(1, "Un", url="/a"),
        morceau(2, "Deux", url="/a"),
        morceau(3, "Trois", url="/b"),
    ]
    emb = FauxEmbedder(32)
    base = await RechercheMemoire.construire(ms, emb)
    r = RechercheParente(base, ms)
    v = (await emb.vectoriser(["Deux"], "question"))[0]
    ps = await r.chercher(v, "fr", 6)
    assert [p.morceau.url for p in ps] == ["/a", "/b"]
    assert "Un" in ps[0].morceau.texte and "Deux" in ps[0].morceau.texte
    assert ps[0].morceau.ancre == ""


def test_decoupages():
    long = " ".join(["Une phrase courte."] * 200)  # 600 mots
    page = Page(
        "/p",
        "fr",
        "x",
        "Titre",
        (Section("Titre", ("A",), "a", long), Section("Titre", ("B",), "b", "court")),
    )
    assert len(decouper(page)) == 3  # 360 mots au plus : la section longue est coupee
    assert len(decouper(page, mode="section")) == 2
    une = decouper(page, mode="page")
    assert len(une) == 1 and une[0].ancre == "" and "A\n" in une[0].texte
    assert len(decouper(page, mode="petit_grand")) == 3


# Corpus servi : page Contact, corpus compact, controle des coordonnees.


def test_page_contact_servie_seulement_sur_reglage():
    ms = [morceau(1, "Prose"), morceau(2, "Disponible en mars 2027", type="contact")]
    assert [m.id for m in morceaux_servis(ms)] == ["m001"]
    assert len(morceaux_servis(ms, inclure_contact=True)) == 2


def test_corpus_compact():
    ms = [
        morceau(1, "Le taux atteint 87,3 % sur le rejeu."),
        morceau(2, "Taux : 87,3 %. Méthode : rejeu.", type="metrique"),
        morceau(3, "Effectif : 1 200 cas.", type="metrique"),
    ]
    assert [m.id for m in compacter(ms)] == ["m001", "m003"]


@pytest.mark.parametrize(
    "fuite", ["Appeler le 06 12 34 56 78, merci", "Ecrire a prenom.nom@exemple.fr", "75011 Paris"]
)
def test_controle_bloquant_des_coordonnees(fuite):
    controler_coordonnees([morceau(1, "Aucune coordonnée, 28 398 944 lignes, 2026.")])
    with pytest.raises(CoordonneeTrouvee):
        controler_coordonnees([morceau(1, fuite)])


def test_corpus_d_exemple_sans_coordonnees(corpus_exemple):
    controler_coordonnees(corpus_exemple[0])


async def test_generateur_sans_schema_reste_compatible(fabriquer_assistant):
    class Ancien:
        nom = "ancien"

        async def rediger(self, consigne, contenu, max_jetons):
            yield Fragment(texte="Réponse [1]")
            yield Fragment(jetons_entree=10, jetons_sortie=2)

    a = await fabriquer_assistant(generateur=Ancien())
    assert fin(await evenements(a, "Le moniteur détecte-t-il les pannes ?"))["chemin"] == "reponse"
