"""Phrases ecrites d'avance : refus, plafonds, carte de contact. Jamais redigees par le modele."""

from __future__ import annotations

TEXTES = {
    "fr": {
        "hors_corpus": "Je ne trouve pas de réponse à cette question dans le portfolio. "
        "Pour tout autre sujet, la page Contact permet d'écrire à Noan.",
        "refus_modele": "Le portfolio ne permet pas de répondre à cette question. "
        "Pour en savoir plus, la page Contact permet d'écrire à Noan.",
        "personnel": "Je ne réponds pas aux questions personnelles (rémunération, âge, adresse, "
        "santé, nationalité, vie privée). Noan y répondra directement : "
        "la page Contact permet de lui écrire.",
        "contact": "Voici les coordonnées de Noan et les liens vers ses CV.",
        "plafond_visiteur": "Vous avez atteint la limite de questions pour le moment. "
        "Réessayez dans une minute, ou consultez le CV.",
        "plafond_global": "L'assistant a atteint son plafond de questions pour aujourd'hui. "
        "Revenez demain, ou consultez le CV.",
        "erreur": "L'assistant n'a pas pu répondre. Réessayez dans un instant.",
        "etape_recherche": "Recherche dans le portfolio",
        "etape_redaction": "Rédaction de la réponse",
    },
    "en": {
        "hors_corpus": "I can't find an answer to this question in the portfolio. "
        "For anything else, the Contact page lets you write to Noan.",
        "refus_modele": "The portfolio doesn't answer this question. "
        "To find out more, the Contact page lets you write to Noan.",
        "personnel": "I don't answer personal questions (pay, age, address, health, "
        "nationality, private life). Noan will answer them directly: "
        "the Contact page lets you write to him.",
        "contact": "Here are Noan's contact details and links to his resumes.",
        "plafond_visiteur": "You have reached the question limit for now. "
        "Try again in a minute, or read the resume.",
        "plafond_global": "The assistant has reached its question cap for today. "
        "Come back tomorrow, or read the resume.",
        "erreur": "The assistant couldn't answer. Please try again shortly.",
        "etape_recherche": "Searching the portfolio",
        "etape_redaction": "Writing the answer",
    },
}


def texte(langue: str, cle: str) -> str:
    return TEXTES.get(langue, TEXTES["fr"])[cle]
