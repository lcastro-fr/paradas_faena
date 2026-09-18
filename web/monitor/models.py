from django.db import models


class PermisosMonitor(models.Model):
    """Modelo sin tabla: sólo aloja permisos que no cuelgan de ninguna."""

    class Meta:
        managed = False
        default_permissions = ()
        permissions = [
            ("ver_monitor_gerencia", "Ver el monitor de gerencia"),
        ]
