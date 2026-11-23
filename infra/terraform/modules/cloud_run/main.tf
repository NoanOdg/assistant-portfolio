# Service Cloud Run de l'assistant : 0 instance au repos, 1 au plus.
#
# Terraform cree le service et en tient la configuration ; la CI deploie les
# images (revision sans trafic, evaluation, bascule). L'image et le trafic sont
# donc ignores par Terraform apres la creation. La version du corpus est
# inscrite dans l'image a la construction : une revision = un corpus.

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

variable "image" {
  description = "Image de depart, remplacee par la CI au premier deploiement."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "compte_execution" {
  type = string
}

variable "compte_deploiement" {
  type = string
}

variable "compte_evaluation" {
  description = "Courriel du compte dont le jeton d'identite ouvre le mode evaluation."
  type        = string
}

variable "secret_session" {
  type = string
}

variable "secret_turnstile" {
  type = string
}

variable "modele_redaction" {
  type    = string
  default = "gemini-3.1-flash-lite"
}

variable "anti_robot" {
  type    = string
  default = "turnstile"
}

variable "public" {
  description = "Ouvrir le service a tous (le site l'appelle depuis le navigateur)."
  type        = bool
  default     = true
}

variable "protection" {
  type    = bool
  default = true
}

resource "google_cloud_run_v2_service" "assistant" {
  project             = var.projet
  name                = var.nom
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = var.protection

  template {
    service_account                  = var.compte_execution
    timeout                          = "35s"
    max_instance_request_concurrency = 10

    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }

    containers {
      image = var.image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      env {
        name  = "ASSISTANT_MOTEUR_MODE"
        value = "vertex"
      }
      env {
        name  = "ASSISTANT_MOTEUR_PROJET_GCP"
        value = var.projet
      }
      env {
        name  = "ASSISTANT_MOTEUR_REGION"
        value = var.region
      }
      env {
        name  = "ASSISTANT_MOTEUR_MODELE_REDACTION"
        value = var.modele_redaction
      }
      env {
        name  = "ASSISTANT_SECURITE_ANTI_ROBOT"
        value = var.anti_robot
      }
      env {
        name  = "ASSISTANT_SECURITE_COMPTE_EVALUATION"
        value = var.compte_evaluation
      }
      env {
        name = "ASSISTANT_SECRET_SESSION"
        value_source {
          secret_key_ref {
            secret  = var.secret_session
            version = "latest"
          }
        }
      }
      env {
        name = "ASSISTANT_SECRET_TURNSTILE"
        value_source {
          secret_key_ref {
            secret  = var.secret_turnstile
            version = "latest"
          }
        }
      }

      startup_probe {
        http_get {
          path = "/sante"
        }
        initial_delay_seconds = 0
        period_seconds        = 2
        failure_threshold     = 15
      }

      liveness_probe {
        http_get {
          path = "/sante"
        }
        period_seconds = 30
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
      template[0].revision,
      template[0].labels,
      traffic,
      client,
      client_version,
    ]
  }
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  count    = var.public ? 1 : 0
  project  = var.projet
  location = var.region
  name     = google_cloud_run_v2_service.assistant.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# La CI deploie des revisions de ce service, et d'aucun autre.
resource "google_cloud_run_v2_service_iam_member" "deploiement" {
  project  = var.projet
  location = var.region
  name     = google_cloud_run_v2_service.assistant.name
  role     = "roles/run.developer"
  member   = "serviceAccount:${var.compte_deploiement}"
}

output "url" {
  value = google_cloud_run_v2_service.assistant.uri
}

output "nom" {
  value = google_cloud_run_v2_service.assistant.name
}
