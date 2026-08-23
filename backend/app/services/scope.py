import re
from enum import StrEnum


class QueryScope(StrEnum):
    TAX = "tax"
    PORTAL = "portal"
    MIXED = "mixed"
    OUT_OF_SCOPE = "out_of_scope"


TAX_TERMS = {
    "tax",
    "vat",
    "income",
    "levy",
    "withholding",
    "return",
    "assessment",
    "deduction",
    "exemption",
    "ird",
    "inland revenue",
    "tin",
    "sri lanka",
    "capital gain",
    "apitt",
    "sscl",
    "stamp duty",
    "taxable",
}
PORTAL_TERMS = {
    "portal",
    "login",
    "click",
    "submit",
    "upload",
    "dashboard",
    "online",
    "e-service",
    "registration",
    "password",
    "where do i",
    "how do i find",
}


def classify_scope(question: str) -> QueryScope:
    text = re.sub(r"\s+", " ", question.lower())
    tax = any(term in text for term in TAX_TERMS)
    portal = any(term in text for term in PORTAL_TERMS)
    if tax and portal:
        return QueryScope.MIXED
    if portal:
        return QueryScope.PORTAL
    if tax:
        return QueryScope.TAX
    return QueryScope.OUT_OF_SCOPE


def extract_tax_year(question: str) -> str | None:
    match = re.search(r"\b(20\d{2})(?:[/-](\d{2,4}))?\b", question)
    if not match:
        return None
    if match.group(2):
        end = match.group(2)
        if len(end) == 2:
            end = match.group(1)[:2] + end
        return f"{match.group(1)}/{end}"
    return match.group(1)
