# Depot d'images Docker. Deux versions gardees, le reste purge (offre gratuite : 0,5 Go).

variable "projet" {
  type = string
}

variable "region" {
  type = string
}

variable "nom" {
  type    = string
  default = "assistant"
}

variable "versions_gardees" {
  type    = number
  default = 2
}

resource "google_artifact_registry_repository" "depot" {
  project                = var.projet
  location               = var.region
  repository_id          = var.nom
  format                 = "DOCKER"
  description            = "Images de l'assistant du portfolio"
  cleanup_policy_dry_run = false

  cleanup_policies {
    id     = "garder-les-plus-recentes"
    action = "KEEP"
    most_recent_versions {
      keep_count = var.versions_gardees
    }
  }

  cleanup_policies {
    id     = "purger-le-reste"
    action = "DELETE"
    condition {
      older_than = "604800s"
    }
  }
}

output "id" {
  value = google_artifact_registry_repository.depot.id
}

output "nom" {
  value = google_artifact_registry_repository.depot.name
}

output "url" {
  value = "${var.region}-docker.pkg.dev/${var.projet}/${google_artifact_registry_repository.depot.repository_id}"
}
