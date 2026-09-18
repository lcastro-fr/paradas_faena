from __future__ import annotations

from django.conf import settings
from django.contrib.auth.models import Group


def sincronizar_grupos(sender, request, user, **kwargs) -> None:
    """Keycloak manda: lo asignado a mano en el admin dura hasta el próximo login."""
    sociallogin = kwargs.get("sociallogin")
    if sociallogin is None:
        return

    crudos = sociallogin.account.extra_data.get(settings.KEYCLOAK_GROUPS_CLAIM) or []
    if isinstance(crudos, str):
        crudos = [crudos]
    nombres = {
        g.removeprefix(settings.KEYCLOAK_GROUP_PREFIX).lstrip("/") for g in crudos
    }

    user.groups.set(Group.objects.filter(name__in=nombres))

    es_super = settings.KEYCLOAK_SUPERUSER_GROUP in nombres
    if (user.is_superuser, user.is_staff) != (es_super, es_super):
        user.is_superuser = user.is_staff = es_super
        user.save(update_fields=["is_superuser", "is_staff"])
