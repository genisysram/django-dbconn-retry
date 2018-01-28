# -* encoding: utf-8 *-
import logging

from django.apps.config import AppConfig
from django.db import utils as django_db_utils
from django.db.backends.base import base as django_db_base
from django.dispatch import Signal

from typing import Union, Tuple, Callable, List  # noqa. flake8 #247


_log = logging.getLogger(__name__)
default_app_config = 'django_dbconn_retry.DjangoIntegration'

pre_reconnect = Signal(providing_args=["connection"])
post_reconnect = Signal(providing_args=["connection"])


_operror_types = ()  # type: Union[Tuple[type], Tuple]
_operror_types += (django_db_utils.OperationalError,)
try:
    import psycopg2
except ImportError:
    pass
else:
    _operror_types += (psycopg2.OperationalError,)

try:
    import sqlite3
except ImportError:
    pass
else:
    _operror_types += (sqlite3.OperationalError,)

try:
    import MySQLdb
except ImportError:
    pass
else:
    _operror_types += (MySQLdb.OperationalError,)


def monkeypatch_django() -> None:
    def ensure_connection_with_retries(self: django_db_base.BaseDatabaseWrapper) -> None:
        if self.connection is not None and hasattr(self.connection, 'closed') and self.connection.closed:
            _log.debug("failed connection detected")
            self.connection = None
        elif ((self.connection is not None and hasattr(self.connection, 'closed') and not self.connection.closed) or
              (self.connection is not None and not hasattr(self.connection, 'closed'))):
            if hasattr(self, '_in_testing'):
                return
            else:
                self._in_testing = True
                self.close_if_unusable_or_obsolete()
                del self._in_testing

        if self.connection is None:
            with self.wrap_database_errors:
                try:
                    self.connect()
                except Exception as e:
                    if isinstance(e, _operror_types):
                        if hasattr(self, "_connection_retries") and self._connection_retries >= 1:
                            _log.error("Reconnecting to the database didn't help %s", str(e))
                            post_reconnect.send(self.__class__, connection=self)
                            raise
                        else:
                            _log.info("Database connection failed. Refreshing...")
                            # mark the retry
                            self._connection_retries = 1
                            # ensure that we retry the connection. Sometimes .closed isn't set correctly.
                            self.connection = None

                            # give libraries like 12factor-vault the chance to update the credentials
                            pre_reconnect.send(self.__class__, connection=self)
                            self.ensure_connection()
                            post_reconnect.send(self.__class__, connection=self)
                    else:
                        _log.debug("Database connection failed, but not due to a known error for dbconn_retry %s",
                                   str(e))
                        raise
                else:
                    # connection successful, reset the flag
                    self._connection_retries = 0

    _log.debug("django_dbconn_retry: monkeypatching BaseDatabaseWrapper")
    django_db_base.BaseDatabaseWrapper.ensure_connection = ensure_connection_with_retries


class DjangoIntegration(AppConfig):
    name = "django_dbconn_retry"

    def ready(self) -> None:
        monkeypatch_django()
