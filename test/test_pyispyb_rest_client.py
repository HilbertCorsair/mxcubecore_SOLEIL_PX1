from datetime import datetime, timedelta, timezone
from json.decoder import JSONDecodeError
from unittest.mock import MagicMock, Mock

import pytest

import jwt
from mxcubecore.HardwareObjects.abstract.PyISPyBRestClient import (
    AuthenticationExpired,
    NoTokenException,
    PyISPyBRestClient,
    PyISPyBUnsuccessfulResponse,
)

REST_ROOT = ""
KEYCLOAK_URL = ""
GRANT_TYPE = "client_credentials"
CLIENT_ID = ""
CLIENT_SECRET = ""

@pytest.fixture
def client():
    client = PyISPyBRestClient(
        rest_root=REST_ROOT,
        keycloak_url=KEYCLOAK_URL,
        grant_type=GRANT_TYPE,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
    )
    client.log = Mock()
    return client


def build_response(
    status_code=200,
    json_data=None,
    text="ok",
):
    response = MagicMock()

    response.status_code = status_code
    response.text = text
    response.url = "http://localhost/ispyb/api/v1/test"

    if isinstance(json_data, Exception):
        response.json.side_effect = json_data
    else:
        response.json.return_value = json_data

    return response


# =========================================================
# AUTHENTICATION
# =========================================================

def test_authenticate_success_real_test():
    client = PyISPyBRestClient(
        rest_root=REST_ROOT,
        keycloak_url=KEYCLOAK_URL,
        grant_type=GRANT_TYPE,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
    )

    client._session.trust_env = False

    client.authenticate()

    userinfo_url = "https://test-helium.synchrotron-soleil.fr:8443/realms/pyispyb/protocol/openid-connect/userinfo"

    claims = jwt.decode(
        client._access_token,
        options={"verify_signature": False},
    )

    claim2 = jwt.decode(
        client._access_token,
        options={"verify_signature": False},
    )

    response = client._session.get(
        userinfo_url,
        headers={
            "Authorization": f"Bearer {client._access_token}"
        }
    )


@pytest.mark.skip
def test_authenticate_success(client):
    keycloak_response = MagicMock()
    keycloak_response.status_code = 200
    keycloak_response.text = '{"access_token":"JDAludM5lGYCYj8Ri","expires_in":300}'
    keycloak_response.json.return_value = {
        "access_token": "JDAludM5lGYCYj8Ri",
        "expires_in": 300,
        "refresh_expires_in": 0,
        "token_type": "Bearer",
        "not-before-policy": 1734028550,
        "scope": "email profile"
    }

    client._session.post = MagicMock(return_value=keycloak_response)

    client._session.trust_env = False

    client.authenticate()

    client._session.post.assert_called_once_with(
        KEYCLOAK_URL,
        data={
            "grant_type": GRANT_TYPE,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        timeout=client._timeout
    )

    assert client._access_token == keycloak_response.json.return_value["access_token"]

    assert (
        client._session.headers["Authorization"]
        == "Bearer JDAludM5lGYCYj8Ri"
    )


@pytest.mark.skip
def test_authenticate_without_token_raises(client):
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.text = "{}"
    fake_response.json.return_value = {}

    client._session.post = MagicMock(return_value=fake_response)

    client.log.debug.assert_called_once_with("Exception ")

    with pytest.raises(NoTokenException):
        client.authenticate()


# =========================================================
# TOKEN REFRESH
# =========================================================

@pytest.mark.skip
def test_refresh_access_token_success(client):
    client._refresh_token = "refresh-token"

    response = build_response(
        json_data={
            "token": "new-access-token",
            "refreshToken": "new-refresh-token",
            "expiresIn": 300,
        }
    )

    client._session.post = MagicMock(return_value=response)

    client._refresh_access_token()

    assert client._access_token == "new-access-token"

    assert client._refresh_token == "new-refresh-token"

    assert client._session.headers["Authorization"] == "Bearer new-access-token"


@pytest.mark.skip
def test_refresh_access_token_without_refresh_token_raises(client):
    client._refresh_token = None

    with pytest.raises(AuthenticationExpired):
        client._refresh_access_token()


@pytest.mark.skip
def test_refresh_access_token_failure_raises(client):
    client._refresh_token = "invalid-token"

    response = build_response(
        status_code=401,
        text="Unauthorized",
    )

    client._session.post = MagicMock(return_value=response)

    with pytest.raises(AuthenticationExpired):
        client._refresh_access_token()


# =========================================================
# GET REQUESTS
# =========================================================

@pytest.mark.skip
def test_get_success(client):
    client._token_expiry = datetime.now(timezone.utc) + timedelta(hours=1)

    response = build_response(json_data={"ok": True})

    client._session.get = MagicMock(return_value=response)

    result = client.get("users")

    assert result == {"ok": True}

    client._session.get.assert_called_once()


@pytest.mark.skip
def test_get_refreshes_expired_token(client):
    client._token_expiry = datetime.now(timezone.utc) - timedelta(seconds=1)

    client._refresh_access_token = MagicMock()

    response = build_response(json_data={"ok": True})

    client._session.get = MagicMock(return_value=response)

    client.get("users")

    client._refresh_access_token.assert_called_once()


@pytest.mark.skip
def test_get_retries_after_401(client):
    client._token_expiry = datetime.now(timezone.utc) + timedelta(hours=1)

    unauthorized = build_response(
        status_code=401,
        text="Unauthorized",
    )

    success = build_response(json_data={"ok": True})

    client._session.get = MagicMock(side_effect=[unauthorized, success])

    client._refresh_access_token = MagicMock()

    result = client.get("users")

    assert result == {"ok": True}

    client._refresh_access_token.assert_called_once()

    assert client._session.get.call_count == 2


# =========================================================
# POST REQUESTS
# =========================================================

@pytest.mark.skip
def test_post_success(client):
    client._token_expiry = datetime.now(timezone.utc) + timedelta(hours=1)

    response = build_response(json_data={"created": True})

    client._session.post = MagicMock(return_value=response)

    result = client.post(
        "users",
        json={"name": "mohsen"},
    )

    assert result == {"created": True}


# =========================================================
# RESPONSE DECODING
# =========================================================

@pytest.mark.skip
def test_decode_json_response_returns_results(client):
    response = build_response(json_data={"results": [1, 2, 3]})

    result = client.decode_json_response(response)

    assert result == [1, 2, 3]


@pytest.mark.skip
def test_decode_json_response_returns_json(client):
    response = build_response(json_data={"ok": True})

    result = client.decode_json_response(response)

    assert result == {"ok": True}


@pytest.mark.skip
def test_decode_json_response_raises_on_http_error(client):
    response = build_response(
        status_code=500,
        text="Server error",
    )

    with pytest.raises(PyISPyBUnsuccessfulResponse):
        client.decode_json_response(response)


@pytest.mark.skip
def test_decode_json_response_raises_on_invalid_json(client):
    response = build_response(
        json_data=JSONDecodeError(
            "invalid json",
            "doc",
            0,
        )
    )

    with pytest.raises(JSONDecodeError):
        client.decode_json_response(response)


# =========================================================
# PROXIES
# =========================================================

@pytest.mark.skip
def test_update_proxies(client):
    proxies = {
        "http": "http://proxy",
        "https": "https://proxy",
    }

    client.update_proxies(proxies)

    assert client._session.proxies["http"] == "http://proxy"

    assert client._session.proxies["https"] == "https://proxy"
