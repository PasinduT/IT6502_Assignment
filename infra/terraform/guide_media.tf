resource "azurerm_storage_account" "guide_media" {
  name                            = substr(replace("st${var.project_name}${var.environment}media", "-", ""), 0, 24)
  resource_group_name             = azurerm_resource_group.main.name
  location                        = azurerm_resource_group.main.location
  account_kind                    = "StorageV2"
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  allow_nested_items_to_be_public = true
  tags                            = local.tags
}

resource "azurerm_storage_container" "guide_images" {
  name                  = var.guide_media_container_name
  storage_account_id    = azurerm_storage_account.guide_media.id
  container_access_type = "blob"
}
