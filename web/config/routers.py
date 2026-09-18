class FaenaRouter:
    """Separa las tablas de Django de las del dominio.

    `default` es la base de la app: auth, sesiones, admin, cuentas. La migra Django.
    `faena` es la de los PLCs: monitoreo_faena y reporting, que las migra dbmate. Django
    sólo lee y, en el admin, escribe sobre las dos tablas de configuración.
    """

    app_db = "default"
    faena_db = "faena"
    apps_de_dominio = frozenset({"catalogo", "monitor"})

    def _base(self, app_label: str) -> str:
        return self.faena_db if app_label in self.apps_de_dominio else self.app_db

    def db_for_read(self, model, **hints) -> str:
        return self._base(model._meta.app_label)

    def db_for_write(self, model, **hints) -> str:
        return self._base(model._meta.app_label)

    def allow_relation(self, obj1, obj2, **hints) -> bool | None:
        if self._base(obj1._meta.app_label) == self._base(obj2._meta.app_label):
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints) -> bool:
        if app_label in self.apps_de_dominio:
            return False
        return db == self.app_db
