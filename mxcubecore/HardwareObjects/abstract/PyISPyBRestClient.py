import logging
from datetime import datetime, timedelta, timezone
from json.decoder import JSONDecodeError
from urllib.parse import urljoin

from requests import Session

log = logging.getLogger("py-ispyb_client")


class NoTokenException(Exception):
    """Exception raised when no token is returned from authentication."""


class PyISPyBUnsuccessfulResponse(Exception):
    """Exception raised when a response from the server is unsuccessful (not 200)."""


class AuthenticationExpired(Exception):
    """Raised when authentication refresh failed."""


class PyISPyBRestClient:
    """REST client for PyISPyB.

    It handles authentication and communication with PyISPyB REST API.
    """

    REFRESH_MARGIN_SECONDS = 60

    def __init__(self, rest_root: str, keycloak_url: str, grant_type: str, client_id: str, client_secret: str,  timeout: int = 5):
        self._rest_root = rest_root
        self._keycloak_url = keycloak_url
        self._grant_type =  grant_type
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout = timeout
        self._session = Session()
        self._access_token = None
        self._token_expiry = None

    def authenticate(self):
        response = self._session.post(
            self._keycloak_url,
            data={
                "grant_type": self._grant_type,
                "client_id": self._client_id,
                "client_secret": self._client_secret
            },
            timeout = self._timeout,
        )
        res = self.decode_json_response(response)
        self._store_tokens(res)

    def post(self, endpoint, **kwargs):
        return self._request(self._session.post, endpoint, **kwargs)

    def get(self, endpoint, **kwargs):
        return self._request(self._session.get, endpoint, **kwargs)

    def patch(self, endpoint, **kwargs):
        return self._request(self._session.patch, endpoint, **kwargs)

    def update_proxies(self, proxy: dict):
        self._session.proxies.update(proxy)

    def _request(self, method, endpoint, **kwargs):  # noqa: FBT002
        if self._is_token_expired():
           self.authenticate()
        timeout = kwargs.pop("timeout", self._timeout)
        url = urljoin(self._rest_root, endpoint)
        response = method(url, timeout=timeout, **kwargs)
        if response.status_code == 401:
            log.warning("Received 401. Re-authenticating.")
            self.authenticate()
            response = method(url, timeout=timeout, **kwargs)
        return self.decode_json_response(response)

    @staticmethod
    def decode_json_response(response):
        log.debug(
            "Received response from. Status code: %s, Response text: %s",
            response.status_code,
            response.text,
        )
        if response.status_code not in (200, 201):
            msg = (
                f"Request failed with code: {response.status_code}. "
                f"Response: {response.text}"
            )
            raise PyISPyBUnsuccessfulResponse(msg)
        try:
            response_json = response.json()
        except JSONDecodeError:
            log.exception(
                "Failed to decode JSON response from. "
                "Status code: %s, Response text: %s",
                response.status_code,
                response.text,
            )
            raise
        if "results" in response_json:
            return response_json["results"]
        return response_json

    def _store_tokens(self, response: dict):
        if not isinstance(response, dict):
            msg = (
                "Authentication response malformed: expected dict, "
                f"got {type(response)}"
            )
            raise NoTokenException(msg)
        access_token = response.get("access_token")
        expires_in = response.get("expires_in", 300)
        if not access_token:
            msg = "Authentication failed. No access token received."
            raise NoTokenException(msg)
        self._access_token = access_token
        self._token_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        self._set_authorization_header(access_token)

    def _is_token_expired(self) -> bool:
        if self._token_expiry is None:
            return True

        return datetime.now(timezone.utc) >= (
            self._token_expiry - timedelta(seconds=self.REFRESH_MARGIN_SECONDS)
        )

    def _set_authorization_header(self, token: str):
        self._session.headers.update({"Authorization": f"Bearer {token}"})
