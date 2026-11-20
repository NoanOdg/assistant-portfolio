"""Regle du depot : aucun tiret long ni demi-long, dans aucun fichier versionne."""

from __future__ import annotations

from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
EXTENSIONS = {
    ".py",
    ".md",
    ".toml",
    ".yaml",
    ".yml",
    ".tf",
    ".tpl",
    ".json",
    ".txt",
    ".mdx",
    ".html",
    ".xml",
    ".cfg",
    ".example",
    "",
}
EXCLUS = {".git", ".venv", "build", ".pytest_cache", ".ruff_cache", ".terraform", "__pycache__"}
INTERDITS = ("\N{EM DASH}", "\N{EN DASH}")


def fichiers():
    for p in RACINE.rglob("*"):
        if (
            p.is_file()
            and not EXCLUS & set(p.relative_to(RACINE).parts)
            and (p.suffix in EXTENSIONS or p.name in {"Dockerfile", ".gitignore"})
        ):
            yield p


def test_aucun_tiret_long():
    fautifs = []
    for p in fichiers():
        try:
            texte = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for n, ligne in enumerate(texte.splitlines(), 1):
            if any(t in ligne for t in INTERDITS):
                fautifs.append(f"{p.relative_to(RACINE)}:{n}")
    assert fautifs == []
