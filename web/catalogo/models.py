from django.core.exceptions import ValidationError
from django.db import connections, models, router


class Plc(models.Model):
    ip = models.GenericIPAddressField("IP", primary_key=True)
    nombre = models.TextField(blank=True, null=True)
    variador = models.BooleanField(
        default=False, help_text="Reporta la frecuencia y el estado de la noria"
    )

    class Meta:
        managed = False
        db_table = '"monitoreo_faena"."plcs"'
        verbose_name = "PLC"
        verbose_name_plural = "PLCs"
        ordering = ["ip"]

    def __str__(self) -> str:
        return f"{self.nombre or 'sin nombre'} ({self.ip})"


class Parametros(models.Model):
    fila = models.BooleanField(primary_key=True, default=True, editable=False)

    reassert_seconds = models.DecimalField(max_digits=10, decimal_places=2)
    poll_seconds = models.DecimalField(max_digits=10, decimal_places=2)
    heartbeat_seconds = models.DecimalField(max_digits=10, decimal_places=2)
    speed_max_interval_seconds = models.DecimalField(max_digits=10, decimal_places=2)
    max_segment_extra_s = models.DecimalField(max_digits=10, decimal_places=2)

    objetivo_cabezas_hora = models.DecimalField(max_digits=10, decimal_places=2)
    rampa_segundos = models.DecimalField(max_digits=10, decimal_places=2)
    circuito_minutos = models.DecimalField(max_digits=10, decimal_places=2)
    margen_jornada_min = models.IntegerField()

    franja_minutos = models.IntegerField()
    est_fae = models.TextField()
    tz = models.TextField("zona horaria")
    monitor_hora_inicio = models.TimeField("el turno arranca a las")

    class Meta:
        managed = False
        db_table = '"reporting"."parametros"'
        verbose_name = "parámetros del tablero"
        verbose_name_plural = "parámetros del tablero"

    def __str__(self) -> str:
        return "Parámetros del tablero"


class PuestoConfig(models.Model):
    ip = models.GenericIPAddressField("IP del PLC")
    tag = models.CharField(max_length=64)
    version = models.IntegerField(default=1)
    orden_linea = models.IntegerField(
        blank=True, null=True, help_text="1 es el primer puesto; N, el de tipificación"
    )
    offset_min = models.DecimalField(
        "offset manual (min)",
        max_digits=8,
        decimal_places=2,
        blank=True,
        null=True,
        help_text="Sólo si el puesto no cae donde lo pone la interpolación",
    )

    class Meta:
        managed = False
        db_table = '"reporting"."puesto_config"'
        verbose_name = "posición de puesto"
        verbose_name_plural = "posiciones de los puestos"
        ordering = ["orden_linea", "ip", "tag"]

    def __str__(self) -> str:
        return f"{self.ip}|{self.tag}|{self.version}"

    def clean(self) -> None:
        """La foreign key rechazaría la terna con un 500; acá es un error de formulario."""
        with connections[router.db_for_read(type(self))].cursor() as cur:
            cur.execute(
                "select 1 from monitoreo_faena.counters_name "
                "where ip = %s and tag = %s and version = %s",
                [self.ip, self.tag, self.version],
            )
            if cur.fetchone() is None:
                raise ValidationError(
                    f"No existe el puesto {self} en monitoreo_faena.counters_name."
                )
