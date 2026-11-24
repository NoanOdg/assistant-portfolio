"""Source de production : le site tel qu'en ligne, lu par son plan (sitemap).

Seul le contenu public entre, par construction. Chaque page est coupee en
sections h2 et h3 d'apres les identifiants que le site pose sur ses titres.
Les deux CV publics (PDF lies depuis la page Contact, absents du plan) entrent
aussi, sans telephone, courriel ni adresse (corpus/pdf.py).
Cette source n'est jamais appelee par les tests (client HTTP simule).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup, Tag

from assistant.corpus.mdx import nettoyer_markdown
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
            if CONTACT.search(chemin):
                sections = sections_contact(sections)
            pages.append(Page(chemin, langue, _projet(chemin), titre, tuple(sections)))
        return pages + self.pages_cv()

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
