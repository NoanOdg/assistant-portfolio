# Cloud Run journalise d'office l'adresse IP et le navigateur de chaque requete
# (journal run.googleapis.com/requests). Cette exclusion l'empeche d'etre
# conserve. L'application ecrit ses propres journaux, sans IP.
#
# Prix : on perd aussi le statut et la latence par requete dans les journaux.
# Les metriques integrees de Cloud Run (requetes, latences) restent disponibles.

variable "projet" {
  type = string
}

variable "service" {
  type = string
}

resource "google_logging_project_exclusion" "requetes_cloud_run" {
  project     = var.projet
  name        = "assistant-sans-ip"
  description = "Journal des requetes Cloud Run de l'assistant (IP des visiteurs) non conserve."
  filter = join(" AND ", [
    "LOG_ID(\"run.googleapis.com/requests\")",
    "resource.type=\"cloud_run_revision\"",
    "resource.labels.service_name=\"${var.service}\"",
  ])
}
