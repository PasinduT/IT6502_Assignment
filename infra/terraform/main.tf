locals {
  name = "${var.project_name}-${var.environment}"
  tags = {
    project     = "Sri Lanka Tax Assistant"
    environment = var.environment
    managed_by  = "terraform"
  }
}

resource "azurerm_resource_group" "main" {
  name     = "rg-${local.name}"
  location = var.location
  tags     = local.tags
}

