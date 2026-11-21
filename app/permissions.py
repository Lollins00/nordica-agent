"""Permessi per ruolo. Questo modulo gira SEMPRE prima di qualunque chiamata all'LLM.

Regole:
- il ruolo "ceo" vede tutto;
- "finance" e "tech" vedono le fonti con acl "all" o che li elencano;
- un ruolo sconosciuto (es. mittente email non riconosciuto) non vede NULLA, nemmeno "all";
- una fonte senza acl è negata (deny by default).
"""

ROLES = ("finance", "tech", "ceo")


def can_see(role: str | None, acl) -> bool:
    if role not in ROLES:
        return False
    if role == "ceo":
        return True
    if acl == "all":
        return True
    if isinstance(acl, (list, tuple)):
        return role in acl
    return False


def visible(role: str | None, items, acl_key: str = "acl") -> list:
    return [i for i in items if can_see(role, i.get(acl_key))]


def acl_label(acl) -> str:
    if acl == "all":
        return "tutti"
    names = {"finance": "Finance", "tech": "Tech", "ceo": "CEO"}
    roles = [names.get(r, r) for r in (acl or [])]
    if "CEO" not in roles:
        roles.append("CEO")
    return ", ".join(roles)
