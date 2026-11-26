"""Source de production : le site tel qu'en ligne, lu par son plan (sitemap).

Seul le contenu public entre, par construction. Chaque page est coupee en
sections h2 et h3, lues comme un visiteur les lit (corpus/lecture.py). Les
redirections et les pages noindex sont ignorees. Chaque pile technique a son
morceau, et une vue "technologies par projet" en est derivee, rangee sur la
page Projets.
Les deux CV publics (PDF lies depuis la page Contact, absents du plan) entrent
aussi, sans telephone, courriel ni adresse (corpus/pdf.py).
Cette source n'est jamais appelee par les tests (client HTTP simule).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup

from assistant.corpus.lecture import PageLue, lire_page
from assistant.corpus.pdf import page_cv, retirer_coordonnees
from assistant.modeles import Page, Section

EXCLUS = (re.compile(r"^/(en/)?demo/"), re.compile(r"^/404"))
# La page Contact entre au corpus avec le type "contact", coordonnees retirees
# comme pour le CV (option 1b de l'etude). Elle n'est servie que si
# `[corpus] inclure_contact` est vrai (corpus/servi.py).
CONTACT = re.compile(r"^/(en/)?contact/?$")
# Texte que Cloudflare laisse a la place d'une adresse masquee.
COURRIEL_MASQUE = re.compile(r"\[\s*email[\s ]*(?:&#160;)?protected\s*\]", re.I)
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def sections_contact(sections: list[Section]) -> list[Section]:
    """Sections de la page Contact sans telephone, courriel, adresse ni commune."""
    propres = []
    for s in sections:
        texte = retirer_coordonnees(COURRIEL_MASQUE.sub("", s.texte)).strip()
        if texte:
            propres.append(Section(s.titre_page, s.chemin, s.ancre, texte, "contact"))
    return propres


PROTECTION_COURRIEL = "/cdn-cgi/l/email-protection#"


def courriel_protege(code: str) -> str:
    """Decode l'adresse que Cloudflare masque dans la page (cle XOR en tete)."""
    try:
        octets = bytes.fromhex(code)
    except ValueError:
        return ""
    if len(octets) < 2:
        return ""
    return bytes(b ^ octets[0] for b in octets[1:]).decode("utf-8", errors="replace")


def _projet(chemin: str) -> str:
    morceaux = [p for p in chemin.strip("/").split("/") if p and p != "en"]
    if len(morceaux) >= 2 and morceaux[0] in {"projets", "projects"}:
        return morceaux[1]
    return morceaux[0] if morceaux else "accueil"


def sections_html(html: str) -> tuple[str, list[Section]]:
    """Titre et sections d'une page (lecture detaillee : corpus/lecture.py)."""
    lue = lire_page(html)
    return lue.titre, lue.sections


VUE = {
    "fr": {
        "titre": "Technologies par projet",
        "intro": (
            "Technologies utilisées dans chaque projet, "
            "d'après la pile technique publiée sur sa page."
        ),
        "page": "Projets",
    },
    "en": {
        "titre": "Technologies by project",
        "intro": (
            "Technologies used in each project, from the technical stack published on its page."
        ),
        "page": "Projects",
    },
}


def vue_technologies(lues: list[tuple[str, PageLue]], langue: str) -> Section | None:
    """Vue transversale : une ligne par projet, ses technologies telles que sa pile
    les publie. Rien n'y est ajoute ni regroupe : elle sert les questions
    "quels projets utilisent X", qu'aucune page ne traite seule."""
    lignes = []
    for _, lue in lues:
        if lue.langue != langue or lue.pile is None:
            continue
        briques = [b for _, bs in lue.pile.lignes for b in bs]
        nom = f"{lue.nom} ({lue.sous_titre})" if lue.sous_titre else lue.nom
        lignes.append(f"{nom}{' : ' if langue == 'fr' else ': '}{', '.join(briques)}.")
    if not lignes:
        return None
    v = VUE[langue]
    return Section(v["page"], (v["titre"],), "", "\n\n".join([v["intro"], *lignes]), "pile")


VERSION = re.compile(r"\s+v?\d+(?:\.\d+)*$")


def vues_par_technologie(lues: list[tuple[str, PageLue]], langue: str) -> list[Section]:
    """Une courte section par technologie : les projets dont la pile la publie.
    Sert "quels projets utilisent X" par un passage tres proche de la question.
    Seul regroupement : le numero de version final ("PostgreSQL 16" et
    "PostgreSQL" sont la meme technologie) ; la graphie publiee reste citee."""
    par_techno: dict[str, tuple[str, list[str]]] = {}
    for _, lue in lues:
        if lue.langue != langue or lue.pile is None:
            continue
        for _, briques in lue.pile.lignes:
            for b in briques:
                nom = VERSION.sub("", b.strip())
                if not nom:
                    continue
                cle = nom.casefold()
                affiche, projets = par_techno.setdefault(cle, (nom, []))
                mention = lue.nom if b.strip() == nom else f"{lue.nom} ({b.strip()})"
                if mention not in projets:
                    projets.append(mention)
    # "Docker Compose" compte aussi pour Docker, cite sous son nom publie.
    for cle, (_, projets) in par_techno.items():
        for autre, (nom_autre, projets_autre) in par_techno.items():
            if autre != cle and autre.startswith(cle + " "):
                for m in projets_autre:
                    projet = m.split(" (")[0]
                    if all(x.split(" (")[0] != projet for x in projets):
                        projets.append(f"{projet} ({nom_autre})")
    v = VUE[langue]
    sections = []
    # Seules les technologies communes a plusieurs projets ont leur section :
    # pour les autres, la pile du projet repond deja, et des centaines de
    # sections d'une ligne evinceraient les vrais passages de la recherche.
    for affiche, projets in par_techno.values():
        if len({m.split(" (")[0] for m in projets}) < 2:
            continue
        if langue == "fr":
            texte = f"Projets qui utilisent {affiche} : {', '.join(projets)}."
        else:
            texte = f"Projects that use {affiche}: {', '.join(projets)}."
        sections.append(Section(v["page"], (v["titre"], affiche), "", texte, "pile"))
    return sections


class SourceSite:
    def __init__(self, site: str, client: httpx.Client | None = None) -> None:
        self.site = site.rstrip("/")
        self.client = client or httpx.Client(
            timeout=20, follow_redirects=True, headers={"User-Agent": "assistant-portfolio"}
        )

    def _urls(self, plan: str) -> list[str]:
        racine = ElementTree.fromstring(self.client.get(plan).raise_for_status().content)  # noqa: S314
        locs = [e.text.strip() for e in racine.findall(".//s:loc", NS) if e.text]
        urls: list[str] = []
        for loc in locs:
            urls.extend(self._urls(loc) if loc.endswith(".xml") else [loc])
        return urls

    def urls(self) -> list[str]:
        for nom in ("/sitemap-index.xml", "/sitemap.xml"):
            r = self.client.get(self.site + nom)
            if r.status_code == 200:
                return self._urls(self.site + nom)
        raise RuntimeError("aucun plan du site trouve")

    def pages(self) -> list[Page]:
        pages = []
        lues: list[tuple[str, PageLue]] = []
        for url in self.urls():
            chemin = urlparse(url).path.rstrip("/") or "/"
            if any(m.search(chemin) for m in EXCLUS):
                continue
            lue = lire_page(self.client.get(url).raise_for_status().text)
            if not lue.indexable:  # redirection ou page noindex : rien a lire
                continue
            lues.append((chemin, lue))
            langue = "en" if chemin == "/en" or chemin.startswith("/en/") else "fr"
            sections = lue.sections
            if CONTACT.search(chemin):
                sections = sections_contact(sections)
            pages.append(Page(chemin, langue, _projet(chemin), lue.titre, tuple(sections)))
        return self.ajouter_vues(pages, lues) + self.pages_cv()

    @staticmethod
    def ajouter_vues(pages: list[Page], lues: list[tuple[str, PageLue]]) -> list[Page]:
        """Range la vue "technologies par projet" sur la page Projets de sa langue."""
        for langue, chemin in (("fr", "/projets"), ("en", "/en/projets")):
            vue = vue_technologies(lues, langue)
            if vue is None:
                continue
            par_techno = vues_par_technologie(lues, langue)
            i = next((k for k, p in enumerate(pages) if p.url == chemin), None)
            if i is None:
                pages.append(Page(chemin, langue, "projets", vue.titre_page, (vue, *par_techno)))
                continue
            p = pages[i]
            vues = [
                Section(p.titre, x.chemin, x.ancre, x.texte, x.type) for x in (vue, *par_techno)
            ]
            pages[i] = Page(p.url, p.langue, p.projet, p.titre, (*p.sections, *vues))
        return pages

    def pages_cv(self) -> list[Page]:
        """Les CV lies depuis la page Contact. Un lien casse fait echouer l'ingestion."""
        pages = []
        for langue, url in self.liens_cv().items():
            contenu = self.client.get(url).raise_for_status().content
            page = page_cv(contenu, urlparse(url).path, langue)
            if page.sections:
                pages.append(page)
        return pages

    def _liens_contact(self) -> list[str]:
        html = self.client.get(self.site + "/contact").raise_for_status().text
        soupe = BeautifulSoup(html, "html.parser")
        liens = [str(a.get("href", "")) for a in soupe.find_all("a")]
        # Adresse masquee par Cloudflare : lien vers /cdn-cgi/... ou attribut data-cfemail.
        for el in soupe.select("[data-cfemail]"):
            liens.append("mailto:" + courriel_protege(str(el.get("data-cfemail", ""))))
        return liens

    def liens_cv(self, liens: list[str] | None = None) -> dict[str, str]:
        liens = self._liens_contact() if liens is None else liens
        pdf = [h for h in liens if urlparse(h).path.lower().endswith(".pdf")]
        cv = {
            "fr": next((h for h in pdf if "CV_" in h), ""),
            "en": next((h for h in pdf if "Resume_" in h), ""),
        }
        return {k: (v if v.startswith("http") else self.site + v) for k, v in cv.items() if v}

    def coordonnees(self) -> dict:
        """Lit la page Contact publiee : courriel, LinkedIn et liens des CV."""
        liens = self._liens_contact()
        courriel = next((h[7:] for h in liens if h.startswith("mailto:") and "@" in h), "")
        if not courriel:
            courriel = next(
                (
                    courriel_protege(h.split("#", 1)[1])
                    for h in liens
                    if h.startswith(PROTECTION_COURRIEL)
                ),
                "",
            )
        linkedin = next((h for h in liens if "linkedin.com" in h), "")
        cv = self.liens_cv(liens)
        return {
            "nom": "",
            "courriel": courriel,
            "linkedin": linkedin,
            "cv": cv,
            "contact": {"fr": self.site + "/contact", "en": self.site + "/en/contact"},
        }
