resource "google_monitoring_notification_channel" "email" {
  display_name = "CARDINAL Gmail alerts"
  type         = "email"

  labels = {
    email_address = var.alert_email
  }
}

locals {
  runtime_alerts = {
    frontend = {
      service = var.frontend_service_name
      seconds = 3600
      name    = "CARDINAL frontend active > 60 minutes"
    }
    backend = {
      service = var.backend_service_name
      seconds = 3600
      name    = "CARDINAL backend active > 60 minutes"
    }
    gemma = {
      service = var.model_service_name
      seconds = 1200
      name    = "CARDINAL CPU Gemma 26B active > 20 minutes"
    }
  }
}

resource "google_monitoring_alert_policy" "runtime" {
  for_each     = local.runtime_alerts
  display_name = each.value.name
  combiner     = "OR"
  enabled      = true

  conditions {
    display_name = "${each.value.service} has at least one instance"

    condition_threshold {
      filter          = "resource.type = \"cloud_run_revision\" AND resource.label.service_name = \"${each.value.service}\" AND metric.type = \"run.googleapis.com/container/instance_count\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "${each.value.seconds}s"

      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_MAX"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["resource.label.service_name"]
      }

      trigger {
        count = 1
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.name]

  alert_strategy {
    notification_channel_strategy {
      renotify_interval          = "1800s"
      notification_channel_names = [google_monitoring_notification_channel.email.name]
    }
  }

  documentation {
    content   = "CARDINAL safety alert: ${each.value.service} has remained instantiated longer than the configured threshold. Check Cloud Run. If this is Gemma and testing is complete, set manual scaling back to zero."
    mime_type = "text/markdown"
  }
}

resource "google_monitoring_alert_policy" "backend_5xx" {
  display_name = "CARDINAL backend elevated 5xx responses"
  combiner     = "OR"
  enabled      = true

  conditions {
    display_name = "backend 5xx count > 5 in 5m"

    condition_threshold {
      filter          = "resource.type = \"cloud_run_revision\" AND resource.label.service_name = \"${var.backend_service_name}\" AND metric.type = \"run.googleapis.com/request_count\" AND metric.label.\"response_code_class\" = \"5xx\""
      comparison      = "COMPARISON_GT"
      threshold_value = 5
      duration        = "0s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_SUM"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["resource.label.service_name"]
      }

      trigger {
        count = 1
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.name]
}
