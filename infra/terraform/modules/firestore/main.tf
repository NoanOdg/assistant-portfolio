# Base Firestore en mode natif, index vectoriels et purge automatique (TTL).
#
# Structure : corpus/{version}/morceaux/{id}. L'index porte sur le groupe de
# collections "morceaux" : il sert toutes les versions du corpus.

variable "projet" {
  type = string
}

variable "region" {
  type = string
}

variable "dimensions" {
  type    = number
  default = 768
}

variable "protection" {
  description = "Protection contre la suppression. A couper avant un destroy volontaire."
  type        = bool
  default     = true
}

resource "google_firestore_database" "base" {
  project                 = var.projet
  name                    = "(default)"
  location_id             = var.region
  type                    = "FIRESTORE_NATIVE"
  delete_protection_state = var.protection ? "DELETE_PROTECTION_ENABLED" : "DELETE_PROTECTION_DISABLED"
  deletion_policy         = var.protection ? "ABANDON" : "DELETE"
}

resource "google_firestore_index" "langue_vecteur" {
  project     = var.projet
  database    = google_firestore_database.base.name
  collection  = "morceaux"
  query_scope = "COLLECTION"

  fields {
    field_path = "langue"
    order      = "ASCENDING"
  }

  # Firestore ajoute __name__ avant le champ vectoriel : l'ecrire evite un
  # remplacement de l'index a chaque apply (vu le 24/11/2026).
  fields {
    field_path = "__name__"
    order      = "ASCENDING"
  }

  fields {
    field_path = "embedding"
    vector_config {
      dimension = var.dimensions
      flat {}
    }
  }
}

resource "google_firestore_index" "langue_projet_vecteur" {
  project     = var.projet
  database    = google_firestore_database.base.name
  collection  = "morceaux"
  query_scope = "COLLECTION"

  fields {
    field_path = "langue"
    order      = "ASCENDING"
  }

  fields {
    field_path = "projet"
    order      = "ASCENDING"
  }

  # Firestore ajoute __name__ avant le champ vectoriel : l'ecrire evite un
  # remplacement de l'index a chaque apply (vu le 24/11/2026).
  fields {
    field_path = "__name__"
    order      = "ASCENDING"
  }

  fields {
    field_path = "embedding"
    vector_config {
      dimension = var.dimensions
      flat {}
    }
  }
}

# Purge automatique : texte des questions (30 jours) et compteurs quotidiens.
resource "google_firestore_field" "ttl" {
  for_each   = toset(["questions", "compteurs"])
  project    = var.projet
  database   = google_firestore_database.base.name
  collection = each.value
  field      = "expire_le"

  ttl_config {}

  # Pas d'index sur ce champ : il ne sert qu'a la purge.
  index_config {}
}

output "nom" {
  value = google_firestore_database.base.name
}
