variable "projet" {
  description = "Identifiant du projet GCP (cree a la main, voir README)."
  type        = string
}

variable "numero_projet" {
  description = "Numero du projet (gcloud projects describe PROJET --format='value(projectNumber)')."
  type        = string
}

variable "region" {
  type    = string
  default = "europe-west1"
}

variable "compte_facturation" {
  description = "Identifiant du compte de facturation (XXXXXX-XXXXXX-XXXXXX)."
  type        = string
}

variable "budget_mensuel" {
  type    = number
  default = 5
}

variable "devise" {
  type    = string
  default = "EUR"
}

variable "depot_github" {
  description = "proprietaire/depot du code de l'assistant."
  type        = string
}

variable "id_proprietaire_github" {
  description = "Identifiant numerique du proprietaire GitHub (gh api users/NOM --jq .id)."
  type        = string
}

variable "modele_redaction" {
  type    = string
  default = "gemini-3.1-flash-lite"
}

variable "seuil_distance" {
  description = "Seuil de refus Vertex, cale au jour 2. -1 tant qu'il n'est pas cale."
  type        = string
  default     = "-1"
}

variable "activer_gke" {
  description = "Etape 2 seulement, le temps d'une mesure."
  type        = bool
  default     = false
}

variable "protection" {
  description = "Protection contre la suppression (Firestore, Cloud Run)."
  type        = bool
  default     = true
}
