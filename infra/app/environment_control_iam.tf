# Least-privilege access used only by the GitHub nightly/stale environment guard
# to send alert mail through the SMTP credentials already held in Secret Manager.
locals {
  cardinal_environment_control_github_sa = "github-cardinal-deployer@${var.project_id}.iam.gserviceaccount.com"
}

resource "google_secret_manager_secret_iam_member" "github_environment_control_smtp" {
  for_each = toset([
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
  ])

  project   = var.project_id
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${local.cardinal_environment_control_github_sa}"
}
