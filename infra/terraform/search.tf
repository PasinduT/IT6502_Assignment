resource "azurerm_search_service" "main" {
  name                = "srch-${local.name}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "free"
  replica_count       = 1
  partition_count     = 1
  tags                = local.tags
}

