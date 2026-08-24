import re


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
