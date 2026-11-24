# Conteneurs de secrets seulement. Les valeurs sont posees a la main
# (gcloud secrets versions add) : une valeur posee par Terraform finirait en
# clair dans l'etat.

variable "projet" {
  type = string
}

variable "region" {
  type = string
}

variable "noms" {
  type    = list(string)
  default = ["assistant-cle-session", "assistant-cle-altcha"]
}

variable "lecteur" {
  description = "Compte de service autorise a lire ces secrets, et eux seuls."
  type        = string
}

resource "google_secret_manager_secret" "secret" {
  for_each  = toset(var.noms)
  project   = var.projet
  secret_id = each.value

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }
}

resource "google_secret_manager_secret_iam_member" "lecture" {
  for_each  = google_secret_manager_secret.secret
  project   = var.projet
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.lecteur}"
}

output "ids" {
  value = { for k, s in google_secret_manager_secret.secret : k => s.secret_id }
}
