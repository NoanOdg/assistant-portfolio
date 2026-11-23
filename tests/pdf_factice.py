"""Fabrique un PDF d'une page, texte en Helvetica, sans dependance ni reseau."""

from __future__ import annotations


def _chaine(texte: str) -> bytes:
    brut = texte.encode("cp1252")
    return b"(" + brut.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


def pdf_factice(lignes: list[str], titre: str = "") -> bytes:
    flux = (
        b"BT /F1 10 Tf 12 TL 50 800 Td "
        + b" ".join(_chaine(ligne) + b" Tj T*" for ligne in lignes)
        + b" ET"
    )
    objets = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Length " + str(len(flux)).encode() + b" >>\nstream\n" + flux + b"\nendstream",
        b"<< /Title " + _chaine(titre) + b" >>",
    ]
    sortie = b"%PDF-1.4\n"
    positions = []
    for i, corps in enumerate(objets, 1):
        positions.append(len(sortie))
        sortie += f"{i} 0 obj\n".encode() + corps + b"\nendobj\n"
    xref = len(sortie)
    sortie += f"xref\n0 {len(objets) + 1}\n0000000000 65535 f \n".encode()
    for pos in positions:
        sortie += f"{pos:010d} 00000 n \n".encode()
    sortie += (
        f"trailer\n<< /Size {len(objets) + 1} /Root 1 0 R /Info 6 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    return sortie
