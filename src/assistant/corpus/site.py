"""Source de production : le site tel qu'en ligne, lu par son plan (sitemap).

Seul le contenu public entre, par construction. Chaque page est coupee en
sections h2 et h3 d'apres les identifiants que le site pose sur ses titres.
Cette source n'est jamais appelee par les tests (client HTTP simule).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup, Tag

from assistant.corpus.mdx import nettoyer_markdown
from assistant.modeles import Page, Section

EXCLUS = (re.compile(r"^/(en/)?demo/"), re.compile(r"^/404"), re.compile(r"^/(en/)?contact/?$"))
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def _projet(chemin: str) -> str:
    morceaux = [p for p in chemin.strip("/").split("/") if p and p != "en"]
    if len(morceaux) >= 2 and morceaux[0] in {"projets", "projects"}:
        return morceaux[1]
    return morceaux[0] if morceaux else "accueil"


def sections_html(html: str) -> tuple[str, list[Section]]:
    soupe = BeautifulSoup(html, "html.parser")
    zone = soupe.find("main") or soupe.body or soupe
    for inutile in zone.find_all(["script", "style", "svg", "noscript", "nav", "form"]):
        inutile.decompose()
    h1 = zone.find("h1")
    titre = h1.get_text(" ", strip=True) if h1 else (soupe.title.string if soupe.title else "")
    sections: list[Section] = []
    h2 = ""
    chemin: tuple[str, ...] = ()
    ancre = ""
    tampon: list[str] = []

    def vider() -> None:
        texte = nettoyer_markdown("\n\n".join(tampon))
        if texte:
            sections.append(Section(titre, chemin, ancre, texte))
        tampon.clear()

    for el in zone.find_all(["h2", "h3", "p", "li", "blockquote", "figcaption", "dd", "dt"]):
        if not isinstance(el, Tag):
            continue
        if el.name in {"h2", "h3"}:
            vider()
            t = el.get_text(" ", strip=True)
            if el.name == "h2":
                h2, chemin = t, (t,)
            else:
                chemin = (h2, t) if h2 else (t,)
            ancre = str(el.get("id") or "")
            continue
        # Un paragraphe dans une liste est deja couvert par son <li>.
        if el.name == "p" and el.find_parent("li"):
            continue
        tampon.append(el.get_text(" ", strip=True))
    vider()
    return titre, sections


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
        for url in self.urls():
            chemin = urlparse(url).path.rstrip("/") or "/"
            if any(m.search(chemin) for m in EXCLUS):
                continue
            titre, sections = sections_html(self.client.get(url).raise_for_status().text)
            langue = "en" if chemin == "/en" or chemin.startswith("/en/") else "fr"
            pages.append(Page(chemin, langue, _projet(chemin), titre, tuple(sections)))
        return pages

    def coordonnees(self) -> dict:
        """Lit la page Contact publiee : courriel, LinkedIn et liens des CV."""
        html = self.client.get(self.site + "/contact").raise_for_status().text
        soupe = BeautifulSoup(html, "html.parser")
        liens = [str(a.get("href", "")) for a in soupe.find_all("a")]
        courriel = next((h[7:] for h in liens if h.startswith("mailto:")), "")
        linkedin = next((h for h in liens if "linkedin.com" in h), "")
        pdf = [h for h in liens if h.lower().endswith(".pdf")]
        cv = {
            "fr": next((h for h in pdf if "CV_" in h), ""),
            "en": next((h for h in pdf if "Resume_" in h), ""),
        }
        cv = {k: (v if v.startswith("http") else self.site + v) for k, v in cv.items() if v}
        return {
            "nom": "",
            "courriel": courriel,
            "linkedin": linkedin,
            "cv": cv,
            "contact": {"fr": self.site + "/contact", "en": self.site + "/en/contact"},
        }
