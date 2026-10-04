class ServiceRouter:
    """The simulator owns a separate database, including in tests."""

    def db_for_read(self, model, **hints):
        return "provider" if model._meta.app_label == "provider" else "default"

    db_for_write = db_for_read

    def allow_relation(self, obj1, obj2, **hints):
        return self.db_for_read(type(obj1)) == self.db_for_read(type(obj2))

    def allow_migrate(self, db, app_label, **hints):
        return db == ("provider" if app_label == "provider" else "default")
