# Alerte de budget : elle previent, elle ne coupe rien. Les vrais plafonds sont
# l'instance unique, le plafond quotidien et le quota Vertex.

variable "compte_facturation" {
  type = string
}

variable "numero_projet" {
  type = string
}

variable "montant" {
  type    = number
  default = 5
}

variable "devise" {
  description = "Doit etre la devise du compte de facturation."
  type        = string
  default     = "EUR"
}

variable "canaux" {
  description = "Canaux de notification Cloud Monitoring (facultatif)."
  type        = list(string)
  default     = []
}

resource "google_billing_budget" "budget" {
  billing_account = var.compte_facturation
  display_name    = "assistant-portfolio"

  budget_filter {
    projects = ["projects/${var.numero_projet}"]
  }

  amount {
    specified_amount {
      currency_code = var.devise
      units         = tostring(var.montant)
    }
  }

  dynamic "threshold_rules" {
    for_each = [0.5, 0.9, 1.0]
    content {
      threshold_percent = threshold_rules.value
    }
  }

  all_updates_rule {
    monitoring_notification_channels = var.canaux
    disable_default_iam_recipients   = false
  }
}
