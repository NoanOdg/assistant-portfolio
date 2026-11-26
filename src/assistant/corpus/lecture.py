"""Lecture d'une page HTML du site : ce qu'un visiteur lit, en texte continu.

Le modele lit chaque morceau seul : il doit se comprendre sans la mise en page.
Regles, dans l'ordre ou elles jouent :

- retire : scripts, styles, dessins, navigation, sommaire du telephone
  (.som-tel), et tout element aria-hidden="true". Le site y range
  ses doublons visuels (titres lettre par lettre, illustrations, barres de
  graphiques, etiquettes du telephone). Le texte des lecteurs d'ecran (.sr),
  lui, reste : c'est souvent la phrase complete d'un graphique. Un element
  hidden reste aussi : c'est une etape de schema qui s'ouvre au clic ;
- un titre h2 ou h3 ouvre une section. Dans une liste de cartes (elements
  freres de meme balise et de meme classe, un titre chacun), la section
  s'ouvre au debut de la carte : ce qui precede le titre (date, cadre) est a
  elle, pas a la carte d'avant ;
- un paragraphe, un element de liste, une cellule : un bloc. Un bloc fait
  seulement de liens (boutons d'action, renvois) est retire ;
- liste de definitions : "terme : valeur", une paire par phrase ;
- tableau : une phrase par ligne, chaque cellule precedee de son en-tete ;
- liste de mots courts (competences) : une seule ligne ;
- pile technique (composant Pile du gabarit projet) : sa propre section,
  titree avec le nom du projet ;
- recollage : un nombre seul rejoint le bloc qui suit (300 + utilisateurs),
  un bloc qui commence par une minuscule rejoint le precedent ; une etiquette
  courte seule dans sa boite avec un paragraphe l'annonce ("Ce qu'on mesure : ...") ;
- doublons : un bloc deja lu dans la section, ou un bloc court dont tous les
  chiffres viennent d'etre lus, est retire ;
- mentions d'interface ("Exemple ecrit pour l'illustration", "Rejouer") : retirees ;
- une introduction de chapitre tres courte rejoint sa premiere sous-section.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from assistant.modeles import Section

RETIRES = "script, style, svg, noscript, template, form, nav, iframe, [aria-hidden=true], .som-tel"
TITRES = ("h2", "h3")
_EN_LIGNE = (
    "a abbr b bdi bdo br button cite code data dfn em i kbd label mark q s samp small "
    "span strong sub sup time u var wbr img"
)
EN_LIGNE = frozenset(_EN_LIGNE.split())
# Balises qui coupent un texte en blocs, meme dans un element en ligne.
COUPANTES = ["p", "div", "ul", "ol", "li", "dl", "table", "figure", "section", "h2", "h3", "h4"]
FEUILLES = frozenset(
    "p li dt dd td th caption figcaption blockquote summary h4 h5 h6".split()  # noqa: SIM905
)
STRUCTURES = ["h2", "h3", "ul", "ol", "dl", "table"]

# Mentions d'interface, retirees ou qu'elles soient dans un bloc.
MENTIONS = re.compile(
    r"\b(?:(?:deux|un) )?exemples? écrits? pour l'illustration\.?"
    r"|\b(?:(?:two|an?) )?examples? written for (?:the )?illustration\.?",
    re.I,
)
# Blocs d'interface, retires quand ils sont seuls.
INTERFACE = re.compile(
    r"^(?:rejouer|replay|lire la mesure|read the measure|illustration|sur cette page|on this page"
    r"|toucher une étape.*|tap a step.*"
    r"|[^.]{0,40}\bécrite?s? pour l'illustration\.?"
    r"|[^.]{0,40}\bwritten for (?:the )?illustration\.?)$",
    re.I,
)
ETIQUETTE_ILLUSTRATION = re.compile(r"^\s*illustration\s*$", re.I)
FIN = re.compile(r"[.!?:;,…]$")
FIN_PHRASE = re.compile(r"[.!?…:]$")
NOMBRE_SEUL = re.compile(r"^[+\-−×÷]?\d[\d\s  ,.]*(?:\s?(?:%|M|k|K|Md|B))?$")
CHIFFRE = re.compile(r"(?<![\w.,])\d+(?:[,.]\d+)*(?!\w)")  # pas le 1 de "C1"
MOTS_DOUBLON = 6  # un bloc court qui ne fait que repeter des chiffres
MOTS_INTRO = 30  # une introduction de chapitre plus courte rejoint sa sous-section
PREFIXE_PILE = {
    "fr": "Pile technique du projet {nom}",
    "en": "Technical stack of the {nom} project",
}


def _sep(langue: str) -> str:
    return " : " if langue == "fr" else ": "


def _norm(texte: str) -> str:
    t = " ".join(texte.split())
    t = re.sub(r"\s+([,)\]])", r"\1", t)
    t = re.sub(r"\s+\.(?=\s|$)", ".", t)  # ".NET" garde son blanc
    return re.sub(r"([(\[])\s+", r"\1", t)


def _coupant(el: Tag) -> bool:
    return el.name not in EN_LIGNE or el.find(COUPANTES) is not None


@dataclass
class Pile:
    libelle: str  # "Pile technique", "Technical stack"
    compte: str  # "22 briques"
    lignes: list[tuple[str, list[str]]]  # (categorie, technologies)


@dataclass
class PageLue:
    titre: str  # "Hydre : trading algorithmique automatisé", sinon l'onglet ou le h1
    nom: str  # h1 : nom court du projet
    sous_titre: str  # la ligne courte qui suit le h1, s'il y en a une
    langue: str
    indexable: bool
    sections: list[Section] = field(default_factory=list)
    pile: Pile | None = None


class _Lecture:
    def __init__(self, titre: str, nom: str, langue: str, cartes: dict[int, Tag]) -> None:
        self.titre, self.nom, self.langue, self.cartes = titre, nom, langue, cartes
        self.sep = _sep(langue)
        self.sections: list[Section] = []
        self.de_carte: list[bool] = []  # la section s'ouvre-t-elle sur une carte ?
        self.h2 = ""
        self.chemin: tuple[str, ...] = ()
        self.ancre = ""
        self.carte = False
        self.blocs: list[str] = []
        self.ouverts: set[int] = set()
        self.pile: Pile | None = None

    # -- texte en ligne -----------------------------------------------------

    def _brut(self, noeuds) -> str:
        """Texte d'une suite de noeuds. Deux balises voisines sont separees par
        un blanc ; un texte colle a une balise reste colle (l'<b>INSEI</b>)."""
        out: list[str] = []
        apres_balise = False
        for c in noeuds:
            if isinstance(c, Comment):
                continue
            if isinstance(c, NavigableString):
                out.append(str(c))
                apres_balise = False
                continue
            if not isinstance(c, Tag):
                continue
            if c.name == "br":
                out.append(" ")
                apres_balise = False
                continue
            t = self._brut(c.children)
            if not t.strip():
                continue
            if c.name == "small" and len(t.split()) > 1:  # "%" reste colle a son nombre
                t = f" ({t.strip()})"
            elif c.name == "q":
                t = f"« {t.strip()} »" if self.langue == "fr" else f"“{t.strip()}”"
            if apres_balise:
                out.append(" ")
            out.append(t)
            apres_balise = True
        return "".join(out)

    def _en_ligne(self, el: Tag) -> str:
        """Texte d'un element sans bloc interne. Un terme en gras en tete, sans
        ponctuation, annonce la suite : "Superviser : ...", "Anglais (C1)"."""
        enfants = [
            c
            for c in el.children
            if not isinstance(c, Comment) and not (isinstance(c, NavigableString) and not c.strip())
        ]
        if len(enfants) > 1 and isinstance(enfants[0], Tag) and enfants[0].name in ("b", "strong"):
            tete = _norm(self._brut([enfants[0]]))
            reste = _norm(self._brut(enfants[1:]))
            if tete and reste and not FIN.search(tete) and not re.match(r"[,.;:)]", reste):
                if len(reste.split()) <= 6:
                    return f"{tete} ({reste})"
                return f"{tete}{self.sep}{reste}"
        return _norm(self._brut(el.children))

    def _que_des_liens(self, noeuds, texte: str) -> bool:
        """Vrai si tout le texte vient de liens : bouton d'action, renvoi, sommaire."""
        liens: list[Tag] = []
        for n in noeuds:
            if isinstance(n, Tag):
                liens.extend([n] if n.name == "a" else n.find_all("a"))
        if not liens:
            return False

        def serre(t: str) -> str:
            return re.sub(r"[\s()]", "", t)

        return serre(" ".join(self._brut([a]) for a in liens)) == serre(texte)

    # -- sections -----------------------------------------------------------

    def ouvrir(self, h: Tag, carte: bool = False) -> None:
        self.vider()
        self.ouverts.add(id(h))
        t = _norm(self._brut(h.children))
        if h.name == "h2":
            self.h2, self.chemin = t, (t,)
        else:
            self.chemin = (self.h2, t) if self.h2 else (t,)
        self.ancre = str(h.get("id") or "")
        self.carte = carte

    def vider(self, type_: str = "prose") -> None:
        texte = "\n\n".join(self._assembler(self.blocs))
        self.blocs = []
        if texte:
            self.sections.append(Section(self.titre, self.chemin, self.ancre, texte, type_))
            self.de_carte.append(self.carte)

    def _assembler(self, blocs: list[str]) -> list[str]:
        propres = []
        for b in blocs:
            b = _norm(MENTIONS.sub("", b))
            if b and not INTERFACE.match(b):
                propres.append(b)
        # un nombre seul rejoint le bloc qui suit
        joints: list[str] = []
        attente = ""
        for b in propres:
            if attente:
                b, attente = f"{attente} {b}", ""
            if NOMBRE_SEUL.match(b):
                attente = b
                continue
            joints.append(b)
        if attente:
            joints.append(attente)
        # un bloc qui commence par une minuscule prolonge le precedent
        suites: list[str] = []
        for b in joints:
            if suites and b[0].islower() and not FIN_PHRASE.search(suites[-1]):
                suites[-1] = f"{suites[-1]}, {b}"
            else:
                suites.append(b)
        # doublons : deja lu dans la section, ou chiffres deja tous lus
        sortie: list[str] = []
        lus = ""
        chiffres: set[str] = set()
        for b in suites:
            nombres = set(CHIFFRE.findall(b))
            if b in lus:
                continue
            if nombres and len(b.split()) <= MOTS_DOUBLON and nombres <= chiffres:
                continue
            sortie.append(b)
            lus += "\n" + b
            chiffres |= nombres
        return sortie

    # -- parcours -----------------------------------------------------------

    def lire(self, el: Tag, cible: list[str] | None = None) -> None:
        """Parcourt les enfants de `el`. Sans `cible`, ecrit dans la section
        courante et ouvre les sections ; avec, recueille les blocs d'une feuille."""
        suite: list = []

        def pousser() -> None:
            if suite:
                t = _norm(self._brut(suite))
                if t and not self._que_des_liens(suite, t):
                    (self.blocs if cible is None else cible).append(t)
                suite.clear()

        for c in el.children:
            if isinstance(c, Comment):
                continue
            if isinstance(c, NavigableString):
                suite.append(c)
                continue
            if not isinstance(c, Tag):
                continue
            if not _coupant(c):
                suite.append(c)
                continue
            pousser()
            dest = self.blocs if cible is None else cible
            if cible is None and id(c) in self.cartes:
                self.ouvrir(self.cartes[id(c)], carte=True)
                self.lire(c)
                continue
            if c.name in TITRES:
                if cible is None and id(c) not in self.ouverts:
                    self.ouvrir(c)
                continue
            classes = c.get("class") or []
            if cible is None and "pile" in classes:
                self.lire_pile(c)
                continue
            if c.name == "table":
                dest.extend(self._tableau(c))
                continue
            if c.name == "dl":
                t = self._definitions(c)
                if t:
                    dest.append(t)
                continue
            if c.name in ("ul", "ol"):
                mots = self._liste_de_mots(c)
                if mots:
                    dest.append(mots)
                    continue
            if c.name in FEUILLES and c.find(STRUCTURES) is None:
                t = self._feuille(c)
                if t:
                    dest.append(t)
                continue
            paire = self._paire(c)
            if paire:
                dest.append(paire)
                continue
            self.lire(c, cible)
        pousser()

    def _feuille(self, el: Tag) -> str:
        if el.find(COUPANTES) is None:
            t = self._en_ligne(el)
            return "" if self._que_des_liens([el], t) else t
        blocs: list[str] = []
        self.lire(el, blocs)
        return _joindre(blocs)

    def _paire(self, el: Tag) -> str:
        """Une etiquette courte et le paragraphe qu'elle annonce, seuls dans leur
        boite : "Ce qu'on mesure : ...", "Sources (WebSocket, aiohttp) : ..."."""
        if any(isinstance(x, NavigableString) and x.strip() for x in el.children):
            return ""
        enfants = [
            x
            for x in el.children
            if isinstance(x, Tag) and _norm(MENTIONS.sub("", x.get_text(" ")))
        ]
        if len(enfants) != 2 or enfants[1].name not in ("p", "span"):
            return ""
        if enfants[0].name not in ("span", "b", "strong", "p") or el.find(STRUCTURES):
            return ""
        if any(x.find(COUPANTES) is not None for x in enfants):
            return ""
        lib, val = self._en_ligne(enfants[0]), self._en_ligne(enfants[1])
        if not lib or not val or len(lib.split()) > 6 or FIN.search(lib):
            return ""
        return f"{lib}{self.sep}{val}"

    def _definitions(self, dl: Tag) -> str:
        paires: list[list] = []
        for x in dl.find_all(["dt", "dd"]):
            t = self._en_ligne(x)
            if not t:
                continue
            if x.name == "dt" or not paires:
                paires.append([t, []])
            else:
                paires[-1][1].append(t)
        phrases = [f"{t}{self.sep}{' ; '.join(v)}" if v else t for t, v in paires]
        return " ".join(_point(p) for p in phrases)

    def _tableau(self, table: Tag) -> list[str]:
        blocs = []
        legende = table.find("caption")
        if legende:
            t = self._en_ligne(legende)
            if t:
                blocs.append(_point(t))
        entetes: list[str] = []
        for tr in table.find_all("tr"):
            cellules = tr.find_all(["th", "td"], recursive=False)
            textes = [self._en_ligne(c) for c in cellules]
            if tr.find_parent("thead") or (not entetes and all(c.name == "th" for c in cellules)):
                entetes = textes
                continue
            parts = []
            for i, t in enumerate(textes):
                if not t:
                    continue
                h = entetes[i] if i < len(entetes) else ""
                parts.append(f"{h}{self.sep}{t}" if h else t)
            if parts:
                blocs.append(" ".join(_point(p) for p in parts))
        return blocs

    def _liste_de_mots(self, liste: Tag) -> str:
        items = liste.find_all("li", recursive=False)
        if len(items) < 3 or any(li.find(COUPANTES) is not None for li in items):
            return ""
        textes = [self._en_ligne(li) for li in items]
        if any(not t or len(t.split()) > 5 or FIN_PHRASE.search(t) for t in textes):
            return ""
        if all(self._que_des_liens([li], t) for li, t in zip(items, textes, strict=True)):
            return ""
        return (" ; " if any("," in t for t in textes) else ", ").join(textes)

    def lire_pile(self, el: Tag) -> None:
        """La pile technique du projet, dans sa propre section, titree avec son nom."""
        tete = el.find(class_="pi-tete")
        gras = tete.find("b") if tete else None
        libelle = _norm(gras.get_text(" ")) if gras else ""
        compte = _norm(self._brut(c for c in tete.children if c is not gras)) if tete else ""
        lignes: list[tuple[str, list[str]]] = []
        for dt in el.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            if dd is None:
                continue
            briques = [_norm(s.get_text(" ")).rstrip(",") for s in dd.find_all(class_="nw")]
            if not briques:
                briques = [b.strip() for b in re.split(r",\s*(?![^()]*\))", self._en_ligne(dd))]
            lignes.append((self._en_ligne(dt), [b for b in briques if b]))
        if not lignes:
            return
        self.pile = Pile(libelle, compte, lignes)
        chemin, ancre, carte = self.chemin, self.ancre, self.carte
        self.vider()
        entete = PREFIXE_PILE[self.langue].format(nom=self.nom or self.titre)
        entete += f" ({compte})." if compte else "."
        self.blocs = [entete] + [f"{cat}{self.sep}{', '.join(b)}." for cat, b in lignes]
        self.chemin = (libelle or entete,)
        self.ancre, self.carte = "", False
        self.vider("pile")
        self.chemin, self.ancre, self.carte = chemin, ancre, carte

    def fusionner_intros(self) -> list[Section]:
        """Une introduction de chapitre tres courte rejoint sa premiere sous-section
        (pas une carte : l'introduction d'une liste de cartes vaut pour toutes)."""
        sortie: list[Section] = []
        sections = self.sections
        i = 0
        while i < len(sections):
            s = sections[i]
            suivante = sections[i + 1] if i + 1 < len(sections) else None
            if (
                suivante is not None
                and len(s.chemin) == 1
                and len(suivante.chemin) == 2
                and suivante.chemin[0] == s.chemin[0]
                and not self.de_carte[i + 1]
                and s.type == suivante.type == "prose"
                and len(s.texte.split()) < MOTS_INTRO
            ):
                sortie.append(
                    Section(
                        suivante.titre_page,
                        suivante.chemin,
                        suivante.ancre,
                        f"{s.texte}\n\n{suivante.texte}",
                        suivante.type,
                    )
                )
                i += 2
                continue
            sortie.append(s)
            i += 1
        return sortie


def _point(t: str) -> str:
    return t if FIN_PHRASE.search(t) else f"{t}."


def _joindre(blocs: list[str]) -> str:
    """Les blocs d'une meme feuille (une carte de chiffre, un element de liste)."""
    out = ""
    for b in blocs:
        if not out:
            out = b
        elif FIN.search(out) or NOMBRE_SEUL.match(out):
            out = f"{out} {b}"
        elif b[0].islower():
            out = f"{out}, {b}"
        else:
            out = f"{out}. {b}"
    return out


def _cartes(zone: Tag) -> dict[int, Tag]:
    """Pour chaque titre de carte, la carte qui le porte : l'ancetre le plus haut
    qui ne contient que ce titre et qui a un frere de meme balise et de meme
    classe portant lui aussi un titre."""
    cartes: dict[int, Tag] = {}
    for h in zone.find_all(TITRES):
        carte = None
        a = h.parent
        while isinstance(a, Tag) and a is not zone and a.parent is not None:
            if len(a.find_all(TITRES)) != 1:
                break
            freres = [
                f
                for f in a.parent.find_all(a.name, recursive=False)
                if f is not a and f.get("class") == a.get("class")
            ]
            if any(f.find(TITRES) is not None for f in freres):
                carte = a
            a = a.parent
        if carte is not None:
            cartes[id(carte)] = h
    return cartes


def lire_page(html: str) -> PageLue:
    soupe = BeautifulSoup(html, "html.parser")
    racine = soupe.find("html")
    langue = str(racine.get("lang", "fr"))[:2] if isinstance(racine, Tag) else "fr"
    langue = langue if langue in ("fr", "en") else "fr"
    robots = soupe.find("meta", attrs={"name": "robots"})
    indexable = not (robots and "noindex" in str(robots.get("content", "")).lower())
    if soupe.find("meta", attrs={"http-equiv": re.compile("refresh", re.I)}):
        indexable = False
    zone = soupe.find("main") or soupe.body or soupe
    for inutile in zone.select(RETIRES):
        inutile.decompose()
    for bouton in zone.find_all("button"):  # commande d'interface : "Rejouer"
        if len(bouton.get_text(" ").split()) <= 2:
            bouton.decompose()
    for etiquette in zone.find_all(string=ETIQUETTE_ILLUSTRATION):  # etiquette d'un dessin
        etiquette.extract()
    lecture = _Lecture("", "", langue, {})
    h1 = zone.find("h1")
    nom = _norm(lecture._brut(h1.children)) if h1 else ""
    # Le sous-titre du projet (ligne courte sous le h1) entre dans le titre de
    # chaque morceau : "INSEI : assistant RAG souverain, en production".
    sous = ""
    suivant = h1.find_next_sibling() if h1 else None
    if isinstance(suivant, Tag) and suivant.name == "p":
        t = _norm(lecture._brut(suivant.children))
        if t and len(t.split()) <= 8 and not FIN_PHRASE.search(t):
            sous = t
            suivant.decompose()
    onglet = _norm(soupe.title.get_text()) if soupe.title else ""
    onglet = re.split(r"\s+·\s+", onglet)[0]
    if sous:
        titre = f"{nom}{_sep(langue)}{sous}"
    else:
        titre = onglet if nom and onglet.startswith(nom) else (nom or onglet)
    lecture = _Lecture(titre, nom, langue, _cartes(zone))
    if h1:
        h1.decompose()
    lecture.lire(zone)
    lecture.vider()
    return PageLue(titre, nom, sous, langue, indexable, lecture.fusionner_intros(), lecture.pile)
