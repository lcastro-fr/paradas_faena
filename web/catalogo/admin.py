from django.contrib import admin

from catalogo.models import Parametros, Plc, PuestoConfig


@admin.register(Plc)
class PlcAdmin(admin.ModelAdmin):
    list_display = ("ip", "nombre", "variador")
    list_filter = ("variador",)


@admin.register(Parametros)
class ParametrosAdmin(admin.ModelAdmin):
    list_display = ("__str__", "monitor_hora_inicio", "objetivo_cabezas_hora")
    fieldsets = (
        ("Monitor en vivo", {"fields": ("monitor_hora_inicio",)}),
        (
            "Muestreo del daemon",
            {
                "description": "Tienen que coincidir con el .env del daemon: max_segment "
                "sale de acá y un valor mal puesto trunca tiempo de parada real.",
                "fields": (
                    "reassert_seconds",
                    "poll_seconds",
                    "heartbeat_seconds",
                    "speed_max_interval_seconds",
                    "max_segment_extra_s",
                ),
            },
        ),
        (
            "Proceso",
            {
                "fields": (
                    "objetivo_cabezas_hora",
                    "rampa_segundos",
                    "circuito_minutos",
                    "margen_jornada_min",
                )
            },
        ),
        ("Presentación", {"fields": ("franja_minutos",)}),
        ("Filtros de producción", {"fields": ("est_fae", "tz")}),
    )

    def has_add_permission(self, request) -> bool:
        return not Parametros.objects.exists()

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(PuestoConfig)
class PuestoConfigAdmin(admin.ModelAdmin):
    list_display = ("orden_linea", "ip", "tag", "version", "offset_min")
    list_display_links = ("tag",)
    list_editable = ("orden_linea", "offset_min")
    list_filter = ("ip",)
    search_fields = ("tag",)
