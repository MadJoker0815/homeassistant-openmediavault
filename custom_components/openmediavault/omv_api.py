"""OpenMediaVault API"""

import logging
from time import time
from threading import Lock
import json
import requests

from voluptuous import Optional

_LOGGER = logging.getLogger(__name__)


# ---------------------------
#   OpenMediaVaultAPI
# ---------------------------
class OpenMediaVaultAPI(object):
    """Handle all communication with OMV."""

    def __init__(
        self, host, username, password, use_ssl=False,
    ):
        """Initialize OMV API."""
        self._host = host
        self._use_ssl = use_ssl
        self._username = username
        self._password = password
        self._protocol = "https" if self._use_ssl else "http"
        self._resource = f"{self._protocol}://{self._host}/rpc.php"

        self.lock = Lock()

        self._connection = None
        self._connected = False
        self._reconnected = False
        self._connection_epoch = 0
        self._connection_retry_sec = 58
        self.error = None
        self.connection_error_reported = False
        self.accounting_last_run = None

    # ---------------------------
    #   has_reconnected
    # ---------------------------
    def has_reconnected(self) -> bool:
        """Check if API has reconnected"""
        if self._reconnected:
            self._reconnected = False
            return True

        return False

    # ---------------------------
    #   connection_check
    # ---------------------------
    def connection_check(self) -> bool:
        """Check if API is connected"""
        if not self._connected or not self._connection:
            if self._connection_epoch > time() - self._connection_retry_sec:
                return False

            if not self.connect():
                return False

        return True

    # ---------------------------
    #   disconnect
    # ---------------------------
    def disconnect(self, location="unknown", error=None):
        """Disconnect API"""
        if not error:
            error = "unknown"

        if not self.connection_error_reported:
            if location == "unknown":
                _LOGGER.error("OpenMediaVault %s connection closed", self._host)
            else:
                _LOGGER.error(
                    "OpenMediaVault %s error while %s : %s", self._host, location, error
                )

            self.connection_error_reported = True

        self._reconnected = False
        self._connected = False
        self._connection = None
        self._connection_epoch = 0

    # ---------------------------
    #   connect
    # ---------------------------
    def connect(self) -> bool:
        """Connect API."""
        self.error = ""
        self._connected = False
        self._connection_epoch = time()
        self._connection = requests.Session()

        self.lock.acquire()
        try:
            response = self._connection.post(
                self._resource,
                data=json.dumps(
                    {
                        "service": "session",
                        "method": "login",
                        "params": {
                            "username": self._username,
                            "password": self._password,
                        },
                    }
                ),
            )
            data = response.json()
            if data["error"] is not None:
                if not self.connection_error_reported:
                    _LOGGER.error(
                        "OpenMediaVault %s unable to connect: %s",
                        self._host,
                        data["error"]["message"],
                    )
                    self.connection_error_reported = True

                self.error_to_strings("%s" % data["error"]["message"])
                self._connection = None
                self.lock.release()
                return False

            if not data["response"]["authenticated"]:
                _LOGGER.error("OpenMediaVault %s authenticated failed", self._host)
                self.error_to_strings()
                self._connection = None
                self.lock.release()
                return False

        except requests.exceptions.ConnectionError as api_error:
            _LOGGER.error(
                "OpenMediaVault %s connection error: %s", self._host, api_error
            )
            self.error_to_strings("%s" % api_error)
            self._connection = None
            self.lock.release()
            return False

        else:
            if self.connection_error_reported:
                _LOGGER.warning("OpenMediaVault %s reconnected", self._host)
                self.connection_error_reported = False
            else:
                _LOGGER.debug("OpenMediaVault %s connected", self._host)

            self._connected = True
            self._reconnected = True
            self.lock.release()

        return self._connected

    # ---------------------------
    #   error_to_strings
    # ---------------------------
    def error_to_strings(self, error=""):
        """Translate error output to error string."""
        self.error = "cannot_connect"
        if error == "Incorrect username or password":
            self.error = "wrong_login"

    # ---------------------------
    #   connected
    # ---------------------------
    def connected(self) -> bool:
        """Return connected boolean."""
        return self._connected

    # ---------------------------
    #   query
    # ---------------------------
    def query(self, service, method, params=None, options=None) -> Optional(list):
        """Retrieve data from OMV"""
        if not self.connection_check():
            return None

        if not params:
            params = {}

        if not options:
            options = {"updatelastaccess": False}

        self.lock.acquire()
        try:
            _LOGGER.debug(
                "OpenMediaVault %s query: %s, %s, %s, %s",
                self._host,
                service,
                method,
                params,
                options,
            )
            response = self._connection.post(
                self._resource,
                data=json.dumps(
                    {
                        "service": service,
                        "method": method,
                        "params": params,
                        "options": options,
                    }
                ),
            )

            data = response.json()
            _LOGGER.debug("OpenMediaVault %s query response: %s", self._host, data)
            if data is not None and data["error"] is not None:
                error_code = data["error"]["code"]
                if error_code == 5001 or error_code == 5002:
                    _LOGGER.debug("OpenMediaVault %s session expired", self._host)
                    if self.connect():
                        self.query(service, method, params, options)

        except requests.exceptions.ConnectionError as api_error:
            _LOGGER.warning("OpenMediaVault %s unable to fetch data", self._host)
            self.disconnect("query", api_error)
            self.lock.release()
            return None

        self.lock.release()
        return data["response"]