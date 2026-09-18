import pathlib

from django.test import SimpleTestCase

WEB = pathlib.Path(__file__).resolve().parent
APPS = sorted(d.name for d in WEB.iterdir() if (d / "apps.py").is_file())


def fuente(app: str, capa: str) -> str:
    archivo = WEB / app / f"{capa}.py"
    if archivo.is_file():
        return archivo.read_text()
    paquete = WEB / app / capa
    if not paquete.is_dir():
        return ""
    return "".join(p.read_text() for p in paquete.rglob("*.py"))


class ReglasDeCapas(SimpleTestCase):
    """Lo que sostienen: una instancia del ORM no llega a la view."""

    def test_domain_no_importa_ninguna_otra_capa(self):
        for app in APPS:
            for capa in ("models", "services", "api"):
                self.assertNotIn(
                    f"{app}.{capa}", fuente(app, "domain"), f"{app}/domain"
                )

    def test_models_no_importa_domain_ni_services(self):
        for app in APPS:
            for capa in ("domain", "services"):
                self.assertNotIn(
                    f"{app}.{capa}", fuente(app, "models"), f"{app}/models"
                )

    def test_las_views_no_importan_models(self):
        for app in APPS:
            self.assertNotIn(f"{app}.models", fuente(app, "api"), f"{app}/api")

    def test_el_descubrimiento_de_apps_no_esta_roto(self):
        self.assertTrue(APPS)
