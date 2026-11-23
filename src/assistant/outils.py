"""Outils de la branche A (agent) : lecture seule, sur le seul corpus public.

Le code impose ce que le modele ne doit pas choisir : la langue de la page, le
seuil de distance (le meme que la branche B), la liste fermee des projets, les
seules URL du corpus. Un argument hors de ces listes recoit une erreur, jamais
un contenu exterieur. La carte de contact est rendue par le code : l'outil ne
renvoie aucune coordonnee au modele.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

from assistant.embeddings import Embedder
from assistant.modeles import Morceau, Passage
from assistant.recherche import Recherche

CHERCHER = "chercher_portfolio"
LIRE = "lire_page"
LISTER = "lister_projets"
CONTACT = "afficher_contact"
NOMS = (CHERCHER, LIRE, LISTER, CONTACT)
REQUETE_MAX = 500


@dataclass
class Execution:
    """Ce que le code retient d'un appel d'outil."""

    passages: list[Passage] = field(default_factory=list)  # matiere rendue au modele
    extra: dict = field(default_factory=dict)  # note ou erreur rendue au modele
    etape: tuple[str, dict] | None = None  # cle de textes.py et ses valeurs
    carte: bool = False
    erreur: str | None = None
    meilleure_distance: float | None = None


class Corpus:
    """Index des pages publiques, construit une fois au demarrage."""

    def __init__(self, morceaux: list[Morceau]) -> None:
        self.pages: dict[tuple[str, str], list[Morceau]] = {}
        for m in morceaux:
            self.pages.setdefault((m.langue, m.url), []).append(m)
        self.projets: list[str] = sorted({m.projet for m in morceaux if m.projet})

    def declarations(self) -> list[dict]:
        """Schemas JSON des outils, transmis au modele (mode AUTO)."""
        return [
            {
                "name": CHERCHER,
                "description": (
                    "Cherche dans le contenu public du portfolio. Renvoie des passages "
                    "numerotes [n] (titre, section, URL, texte), ou aucun si rien n'est "
                    "assez proche de la requete."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "requete": {
                            "type": "string",
                            "description": "Ce qu'il faut trouver, en quelques mots.",
                        },
                        "projet": {
                            "type": "string",
                            "enum": self.projets,
                            "description": "Facultatif : limite la recherche a un projet.",
                        },
                    },
                    "required": ["requete"],
                },
            },
            {
                "name": LIRE,
                "description": (
                    "Lit une page entiere du portfolio, section par section. L'URL doit "
                    "venir d'un passage ou de lister_projets."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL d'une page du site."}
                    },
                    "required": ["url"],
                },
            },
            {
                "name": LISTER,
                "description": "Renvoie la fiche de chaque projet publie, en passages numerotes.",
            },
            {
                "name": CONTACT,
                "description": (
                    "Affiche la carte de contact de Noan (courriel, LinkedIn, CV). "
                    "La page l'affiche elle-meme : ne recopie aucune coordonnee."
                ),
            },
        ]


class Outils:
    """Execution des outils pour une question. La langue est celle de la page."""

    def __init__(
        self,
        corpus: Corpus,
        embedder: Embedder,
        recherche: Recherche,
        *,
        langue: str,
        passages: int,
        seuil_distance: float,
        appliquer_seuil: bool,
        site: str,
        caracteres_page: int,
    ) -> None:
        self.corpus = corpus
        self.embedder = embedder
        self.recherche = recherche
        self.langue = langue
        self.k = passages
        self.seuil = seuil_distance
        self.appliquer_seuil = appliquer_seuil
        self.site = site.rstrip("/")
        self.caracteres_page = caracteres_page

    async def executer(self, nom: str, arguments: object) -> Execution:
        args = arguments if isinstance(arguments, dict) else {}
        if nom == CHERCHER:
            return await self._chercher(args)
        if nom == LIRE:
            return self._lire(args)
        if nom == LISTER:
            return self._lister()
        if nom == CONTACT:
            return Execution(
                extra={"affichee": True, "note": "La page affiche la carte de contact."},
                carte=True,
            )
        return Execution(extra={"erreur": "outil inconnu"}, erreur="outil_inconnu")

    async def _chercher(self, args: dict) -> Execution:
        requete = args.get("requete")
        if not isinstance(requete, str) or not requete.strip():
            return Execution(extra={"erreur": "requete vide"}, erreur="argument")
        projet = args.get("projet") or None
        if projet is not None and projet not in self.corpus.projets:
            return Execution(extra={"erreur": "projet inconnu"}, erreur="argument")
        etape = ("etape_recherche", {})
        vecteur = (await self.embedder.vectoriser([requete.strip()[:REQUETE_MAX]], "question"))[0]
        trouves = await self.recherche.chercher(vecteur, self.langue, self.k, projet)
        meilleure = trouves[0].distance if trouves else None
        # Meme regle que la branche B : le meilleur passage au-dessus du seuil,
        # rien ne passe. Le modele ne voit pas ces passages.
        if not trouves or (self.appliquer_seuil and trouves[0].distance > self.seuil):
            return Execution(
                extra={"note": "aucun passage assez proche"},
                etape=etape,
                meilleure_distance=meilleure,
            )
        return Execution(passages=trouves, etape=etape, meilleure_distance=meilleure)

    def _chemin(self, url: str) -> str | None:
        u = url.strip()
        if u.startswith(self.site):
            u = u[len(self.site) :]
        elif urlparse(u).scheme or u.startswith("//"):
            return None  # un autre site : jamais lu
        chemin = urlparse(u).path
        if not chemin.startswith("/"):
            return None
        return chemin.rstrip("/") or "/"

    def _lire(self, args: dict) -> Execution:
        url = args.get("url")
        chemin = self._chemin(url) if isinstance(url, str) else None
        morceaux = self.corpus.pages.get((self.langue, chemin)) if chemin else None
        if not morceaux:
            return Execution(
                extra={"erreur": "page absente du portfolio dans cette langue"},
                erreur="page_inconnue",
            )
        retenus, total = [], 0
        for m in morceaux:
            taille = len(m.texte_indexe)
            if retenus and total + taille > self.caracteres_page:
                break
            retenus.append(m)
            total += taille
        extra = {"tronquee": True} if len(retenus) < len(morceaux) else {}
        return Execution(
            passages=[Passage(m, 0.0) for m in retenus],
            extra=extra,
            etape=("etape_lecture", {"titre": morceaux[0].titre}),
        )

    def _lister(self) -> Execution:
        fiches = []
        for (langue, url), morceaux in sorted(self.corpus.pages.items()):
            if langue != self.langue or "/projets/" not in url:
                continue
            fiche = next((m for m in morceaux if m.type == "fiche"), morceaux[0])
            fiches.append(Passage(fiche, 0.0))
        return Execution(passages=fiches, etape=("etape_liste", {}))
