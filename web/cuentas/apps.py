from django.apps import AppConfig


class CuentasConfig(AppConfig):
    name = "cuentas"

    def ready(self) -> None:
        from allauth.account.signals import user_logged_in

        from cuentas.signals import sincronizar_grupos

        user_logged_in.connect(sincronizar_grupos)
