resource "azurerm_storage_account" "main" {
  name                            = substr(replace("st${var.project_name}${var.environment}", "-", ""), 0, 24)
  resource_group_name             = azurerm_resource_group.main.name
  location                        = azurerm_resource_group.main.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  tags                            = local.tags
}

resource "azurerm_storage_container" "documents" {
  name                  = "tax-source-documents"
  storage_account_id    = azurerm_storage_account.main.id
  container_access_type = "private"
}

