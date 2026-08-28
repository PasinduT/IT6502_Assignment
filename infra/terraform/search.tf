resource "azurerm_search_service" "main" {
  name                = "srch-${local.name}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "free"
  tags                = local.tags
}
