from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connections, transaction


class Command(BaseCommand):
    help = "Aplica reporting/*.sql en orden. Idempotente: son CREATE OR REPLACE VIEW."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--check",
            action="store_true",
            help="Valida que apliquen y hace rollback, sin dejar nada.",
        )
        parser.add_argument("--database", default="faena")

    def handle(self, *args, **opciones) -> None:
        directorio = Path(settings.BASE_DIR).parent / "reporting"
        # Sólo los numerados: verificacion.sql usa \echo, que es de psql y no de psycopg.
        archivos = sorted(directorio.glob("[0-9]*.sql"))
        if not archivos:
            raise CommandError(f"No hay vistas en {directorio}")

        alias = opciones["database"]
        with (
            transaction.atomic(using=alias),
            connections[alias].cursor() as cursor,
        ):
            for archivo in archivos:
                try:
                    cursor.execute(archivo.read_text())
                except DatabaseError as exc:
                    raise CommandError(f"{archivo.name}: {exc}") from exc
                self.stdout.write(f"  {archivo.name}")
            if opciones["check"]:
                transaction.set_rollback(True, using=alias)

        verbo = "verificadas" if opciones["check"] else "aplicadas"
        self.stdout.write(self.style.SUCCESS(f"{len(archivos)} {verbo}"))
