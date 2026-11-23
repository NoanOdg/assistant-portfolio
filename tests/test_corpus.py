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
    }
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
