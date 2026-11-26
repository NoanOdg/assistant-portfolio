from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from assistant.corpus import rendu
from assistant.corpus.decoupage import decouper
from assistant.corpus.ingestion import (
    GardeSuppression,
    construire,
    ecrire,
    lire,
    verifier_suppressions,
)
from assistant.corpus.mdx import SourceMdx
from assistant.corpus.site import SourceSite
from assistant.modeles import Page, Section

from .conftest import DONNEES
from .pdf_factice import pdf_factice


def test_rendu_des_valeurs_comme_le_site():
    assert rendu.nombre(28398944, "fr") == "28 398 944"
    assert rendu.nombre(28398944, "en") == "28,398,944"
    assert rendu.nombre(24.42795, "en") == "24.4279"  # comme Intl sur le double
    assert rendu.nombre(0.489, "fr") == "0,489"
    assert (
        rendu.valeur_metrique(
            {"valeur": 0.316, "affichage": "pourcentage", "unite": {"fr": "points"}}, "fr"
        )
        == "31,6 points"
    )
    assert rendu.periode("2025-09 → 2026-06", "fr") == "de septembre 2025 à juin 2026"
    assert rendu.ancre("Rien ne sort, rien n'est payé") == "rien-ne-sort-rien-nest-payé"


def test_les_chiffres_des_composants_sont_resolus(source_exemple):
    pages = {(p.projet, p.langue): p for p in source_exemple.pages()}
    fr = pages[("exemple", "fr")]
    texte = "\n".join(s.texte for s in fr.sections)
    assert "87,3 %" in texte
    assert "1,3 M (1 250 000) capteurs" in texte
    assert "<Metrique" not in texte and "<" not in texte
    assert "Ce que c'est. Un moniteur de pannes" in texte
    en = "\n".join(s.texte for s in pages[("exemple", "en")].sections)
    assert "87.3 %" in en


def test_rien_de_prive_n_entre_au_corpus(corpus_exemple):
    morceaux, _ = corpus_exemple
    tout = "\n".join(m.texte_indexe for m in morceaux)
    for interdit in (
        "fiche-secrete",
        "relecteur interne",
        "ne doit jamais sortir",
        "contact@exemple.test",
        "Un commentaire",
    ):
        assert interdit not in tout


def test_sections_ancres_et_metadonnees(corpus_exemple):
    morceaux, coord = corpus_exemple
    fr = [m for m in morceaux if m.langue == "fr" and m.projet == "exemple"]
    ancres = {m.ancre for m in fr}
    assert {"le-problème-des-arrêts", "le-taux-de-détection"} <= ancres
    taux = next(m for m in fr if m.ancre == "le-taux-de-détection")
    assert taux.contexte == (
        "Surveillance : un moniteur de pannes > Ce que le moniteur a mesuré > Le taux de détection"
    )
    assert taux.lien == "/projets/exemple#le-taux-de-détection"
    assert {m.type for m in morceaux} == {"prose", "fiche", "metrique", "parcours"}
    assert all(m.url.startswith("/en/") for m in morceaux if m.langue == "en")
    assert coord["courriel"] == "contact@exemple.test"
    assert coord["cv"]["en"].endswith("/Resume_Exemple.pdf")
    assert len({m.id for m in morceaux}) == len(morceaux)


def test_fiche_du_projet(corpus_exemple):
    morceaux, _ = corpus_exemple
    fiche = next(m for m in morceaux if m.type == "fiche" and m.langue == "fr")
    assert "Équipe : 3 personnes." in fiche.texte
    assert "Technologies : Python, Docker." in fiche.texte
    assert "de septembre 2025 à janvier 2026" in fiche.texte
    parcours = [m for m in morceaux if m.projet == "parcours" and m.langue == "fr"]
    assert any("à partir d'avril 2023" in m.texte for m in parcours)
    assert any("TOEIC 950" in m.texte for m in parcours)


def test_decoupage_d_une_section_longue():
    phrase = "Une phrase de dix mots pour remplir la section longue. "
    page = Page("/projets/x", "fr", "x", "X", (Section("X", ("Longue",), "longue", phrase * 120),))
    morceaux = decouper(page, mots_max=100)
    assert len(morceaux) > 1
    assert all(len(m.texte.split()) <= 100 for m in morceaux)
    assert all(m.contexte == "X > Longue" for m in morceaux)


def test_garde_des_suppressions(tmp_path, corpus_exemple):
    morceaux, coord = corpus_exemple
    sortie = tmp_path / "corpus.jsonl"
    ecrire(morceaux, coord, sortie)
    assert lire(sortie) == morceaux
    assert verifier_suppressions(morceaux, morceaux) == 0.0
    with pytest.raises(GardeSuppression):
        verifier_suppressions(morceaux, morceaux[: len(morceaux) // 2])


def test_ingestion_relancable(tmp_path, source_exemple):
    a, _ = construire(source_exemple)
    b, _ = construire(source_exemple)
    assert [m.empreinte for m in a] == [m.empreinte for m in b]


CV_FACTICES = {
    "/CV_Exemple.pdf": pdf_factice(
        ["Moniteur de pannes en production", "+33 6 12 34 56 78", "contact@exemple.test"],
        titre="CV Exemple",
    ),
    "/Resume_Exemple.pdf": pdf_factice(["Failure monitor in production"], titre="Resume Exemple"),
}


def _client_site() -> httpx.Client:
    fichiers = {
        "/sitemap-index.xml": "sitemap-index.xml",
        "/sitemap-0.xml": "sitemap-0.xml",
        "/projets/exemple/": "projet-fr.html",
        "/en/projets/exemple/": "projet-en.html",
        "/contact": "contact.html",
        "/contact/": "contact.html",
    }

    def repondre(requete: httpx.Request) -> httpx.Response:
        if requete.url.path in CV_FACTICES:
            return httpx.Response(200, content=CV_FACTICES[requete.url.path])
        nom = fichiers.get(requete.url.path)
        if not nom:
            return httpx.Response(404)
        return httpx.Response(200, content=(DONNEES / "site" / nom).read_bytes())

    return httpx.Client(transport=httpx.MockTransport(repondre))


def test_source_site_lit_le_plan_et_les_sections():
    source = SourceSite("https://exemple.test", client=_client_site())
    pages = source.pages()
    assert {(p.url, p.langue, p.projet) for p in pages} == {
        ("/projets/exemple", "fr", "exemple"),
        ("/en/projets/exemple", "en", "exemple"),
        ("/CV_Exemple.pdf", "fr", "cv"),
        ("/Resume_Exemple.pdf", "en", "cv"),
        ("/contact", "fr", "contact"),
    }
    contact = next(p for p in pages if p.projet == "contact")
    texte_contact = " ".join(s.texte for s in contact.sections)
    assert "mars 2027" in texte_contact
    assert "07 00" not in texte_contact and "@" not in texte_contact
    assert {s.type for s in contact.sections} == {"contact"}
    cv = next(p for p in pages if p.projet == "cv" and p.langue == "fr")
    texte_cv = " ".join(s.texte for s in cv.sections)
    assert "Moniteur de pannes" in texte_cv
    assert "+33" not in texte_cv and "@" not in texte_cv
    fr = next(p for p in pages if p.langue == "fr")
    assert fr.titre == "Surveillance : un moniteur de pannes"
    par_ancre = {s.ancre: s for s in fr.sections}
    assert "87,3 %" in par_ancre["le-taux-de-détection"].texte
    assert "var x" not in " ".join(s.texte for s in fr.sections)
    assert par_ancre["le-taux-de-détection"].texte.count("Rejeu") == 1
    coord = source.coordonnees()
    assert coord["courriel"] == "contact@exemple.test"
    assert coord["cv"]["fr"] == "https://exemple.test/CV_Exemple.pdf"


CONTENU_REEL = Path(
    os.environ.get(
        "ASSISTANT_CONTENU_PORTFOLIO",
        Path(__file__).resolve().parents[2] / "Carriere" / "portfolio" / "src" / "content",
    )
)


@pytest.mark.skipif(not CONTENU_REEL.exists(), reason="contenu du portfolio absent")
def test_contenu_reel_du_portfolio():
    """Le vrai contenu passe, sans fuite des champs internes (lecture seule)."""
    morceaux, coord = construire(SourceMdx(CONTENU_REEL, "https://noan-ouedraogo.dev"))
    assert len(morceaux) > 100
    assert {m.langue for m in morceaux} == {"fr", "en"}
    tout = "\n".join(m.texte_indexe for m in morceaux)
    assert "docs/fiche-preuves" not in tout
    assert "<Metrique" not in tout
    assert coord["courriel"]


# Lecture du site refondu (26/11/2026) : ce qu'un visiteur lit, en texte continu.

PAGE_PROJET = """<!doctype html><html lang="fr"><head><title>Moniteur · Noan</title></head>
<body><main>
<div><h1><span class="sr">Moniteur</span><span class="lettres" aria-hidden="true">
<span>M</span><span>o</span><span>n</span></span></h1>
<p class="pj-sous">surveillance de pannes</p></div>
<ul class="ccb"><li><span aria-hidden="true"><i></i></span><div class="cc-n"><span>87,3</span>
<small>%</small></div><p>des pannes détectées</p><p>Sur des journaux jamais vus.</p></li></ul>
<dl class="g3a"><div><dt>Cadre</dt><dd>Projet d'école</dd></div>
<div><dt>Équipe</dt><dd>Trois</dd></div></dl>
<aside><nav><p>Sur cette page</p><ol><li><a href="#p">Problème</a></li></ol></nav>
<div class="pile"><p class="pi-tete"><b>Pile technique</b><span>3 briques</span></p>
<dl><div><dt>Service</dt>
<dd><span class="nw">Python,</span><span class="nw">Docker</span></dd></div>
<div><dt>Données</dt><dd><span class="nw">PostgreSQL</span></dd></div></dl></div></aside>
<details class="som-tel"><summary>Chapitre Sommaire</summary><ol><li><a href="#p">Problème</a></li>
</ol></details>
<section id="p" class="chap"><h2 id="h-probleme">Problème</h2>
<p>Les pannes coûtent cher.</p></section>
<section id="a" class="chap"><h2 id="h-architecture">Architecture</h2>
<div class="sf-tete"><span>Deux exemples écrits pour l'illustration</span>
<button>Rejouer</button></div>
<div class="etape" hidden><div><p><b>Lire</b><span>Kafka</span></p>
<p>Le flux est lu en continu.</p>
<p>Exemple écrit pour l'illustration.</p></div></div>
<div class="i4"><p>Pannes détectées <small>sur 200 journaux</small></p>
<p><span aria-hidden="true">87,3</span>
<span class="sr">Pannes détectées : 87,3 %, un gain.</span></p>
<span>87,3 %</span></div>
<table><caption>Deux décisions</caption><thead><tr><th>Décision</th><th>Option écartée</th></tr>
</thead><tbody><tr><th>Un seuil appris</th><td>Un seuil fixe</td></tr></tbody></table></section>
</main></body></html>"""

PAGE_LISTE = """<html lang="fr"><head><title>Projets · Noan</title></head><body><main>
<h1>Projets</h1><ul>
<li class="pc"><p><span>École</span><span>2025</span></p><h2><a href="/projets/a">Alpha</a></h2>
<p>Premier projet.</p><div class="ch"><div><span>300</span></div><p>utilisateurs</p></div>
<div aria-hidden="true"><p>illustration</p></div></li>
<li class="pc"><p><span>Personnel</span><span>2026</span></p><h2><a href="/projets/b">Beta</a></h2>
<p>Second projet.</p></li></ul>
<p><a href="/parcours">Voir le parcours</a></p></main></body></html>"""


def test_lecture_d_une_page_projet():
    from assistant.corpus.lecture import lire_page

    lue = lire_page(PAGE_PROJET)
    assert lue.titre == "Moniteur : surveillance de pannes" and lue.nom == "Moniteur"
    par_chemin = {s.chemin: s for s in lue.sections}
    tout = "\n".join(s.texte for s in lue.sections)
    # titres lettre par lettre, sommaires et commandes d'interface : jamais lus
    assert "M o n" not in tout and "Sommaire" not in tout and "Sur cette page" not in tout
    assert "Rejouer" not in tout and "illustration" not in tout
    assert "Problème" not in tout  # le titre du chapitre est dans le chemin, pas dans le texte
    tete = par_chemin[()].texte
    assert "87,3 % des pannes détectées. Sur des journaux jamais vus." in tete
    assert "Cadre : Projet d'école. Équipe : Trois." in tete
    pile = par_chemin[("Pile technique",)]
    assert pile.type == "pile"
    assert pile.texte.startswith("Pile technique du projet Moniteur (3 briques).")
    assert "Service : Python, Docker." in pile.texte
    archi = par_chemin[("Architecture",)].texte
    assert "Lire (Kafka) : Le flux est lu en continu." in archi  # etape cachee, ouverte au clic
    assert "Pannes détectées (sur 200 journaux)" in archi
    assert archi.count("87,3") == 1  # le doublon visuel du chiffre est retire
    assert "Décision : Un seuil appris. Option écartée : Un seuil fixe." in archi
    assert lue.pile is not None and lue.pile.lignes[0] == ("Service", ["Python", "Docker"])


def test_chaque_carte_garde_ce_qui_precede_son_titre():
    from assistant.corpus.lecture import lire_page

    sections = {s.chemin: s.texte for s in lire_page(PAGE_LISTE).sections}
    assert sections[("Alpha",)].startswith("École 2025")
    assert "300 utilisateurs" in sections[("Alpha",)]
    assert "Personnel" not in sections[("Alpha",)]
    assert sections[("Beta",)].startswith("Personnel 2026")
    assert "Voir le parcours" not in "\n".join(sections.values())  # bouton d'action


def test_vue_technologies_derivee_des_piles():
    from assistant.corpus.lecture import lire_page
    from assistant.corpus.site import SourceSite

    lues = [("/projets/moniteur", lire_page(PAGE_PROJET))]
    liste = Page("/projets", "fr", "projets", "Projets", (Section("Projets", (), "", "x"),))
    pages = SourceSite.ajouter_vues([liste], lues)
    vue = pages[0].sections[-1]
    assert vue.chemin == ("Technologies par projet",) and vue.type == "pile"
    assert "Moniteur (surveillance de pannes) : Python, Docker, PostgreSQL." in vue.texte
    assert all(p.langue == "fr" for p in pages)  # aucune pile anglaise : aucune vue anglaise


def test_redirection_et_noindex_ignorees():
    from assistant.corpus.lecture import lire_page

    redirection = (
        '<html><head><meta http-equiv="refresh" content="0; url=/b"></head><body></body></html>'
    )
    assert lire_page(redirection).indexable is False
    assert (
        lire_page('<html><head><meta name="robots" content="noindex"></head></html>').indexable
        is False
    )
    assert lire_page(PAGE_PROJET).indexable is True


def test_un_texte_publie_deux_fois_entre_une_fois():
    from assistant.corpus.ingestion import sans_doublons
    from assistant.modeles import Morceau

    def m(url, texte, ident):
        return Morceau(ident, url, "", "t", "c", texte, "fr", "p", "prose", "e")

    gardes = sans_doublons(
        [m("/", "Carte Alpha", "1"), m("/projets", "Carte Alpha", "2"), m("/", "Autre", "3")]
    )
    assert [(x.id, x.url) for x in gardes] == [("2", "/projets"), ("3", "/")]
