"""InBody mobile API client.

Reverse-engineered from the InBody Android app (v2.8.31_914) via a mitmproxy
capture. Communicates with the regional lookinbody.com API hosts using clean
JSON payloads.

Auth model:
  - POST /CommonAPI/GetCountryInfoV2 (on appapicommon.lookinbody.com) returns a
    per-country host table; the row with Type=="API" gives the regional API base
    (US -> https://appapiusav2.lookinbody.com).
  - POST {api}/V2/Main/GetLoginWithSyncDataPartV2 with LoginID/LoginPW returns a
    JWT `Token` (24h lifetime), a `RefreshToken`, and the user profile incl. UID.
  - Authenticated calls send `Authorization: Bearer <JWT>`.

This client scopes itself to authentication and read-only InBody scan data.
"""

import logging
import os

import httpx

logger = logging.getLogger(__name__)

COMMON_URL = "https://appapicommon.lookinbody.com"

# Sentinel sync datetime the app uses to request "everything".
_FULL_SYNC_DATETIME = "1990-01-01 11:11:11"

# Boilerplate device/app envelope sent with every request body.
_APP_ENVELOPE = {
    "Language": "en",
    "LogCode": "",
    "AppType": "Android",
    "OSVersion": "14",
    "PhoneModel": "Google Pixel 6 Pro",
    "RegAppType": "InBody",
    "AppVersion": "2.8.31_914",
}

# User agents observed in the capture.
_UA_OKHTTP = "okhttp/4.12.0"
_UA_DALVIK = "Dalvik/2.1.0 (Linux; U; Android 14; Pixel 6 Pro Build/AP2A.240905.003.F1)"


class InBodyError(Exception):
    """Raised when an InBody API call fails."""


class InBodyClient:
    """Stateful client for the InBody mobile API.

    Resolves the regional API host, logs in with ID/password, caches the JWT in
    memory, and re-authenticates automatically when the session expires.
    """

    def __init__(self) -> None:
        self._api_base: str | None = None
        self._country_number: str | None = None
        self._token: str | None = None
        self._refresh_token: str | None = None
        self._uid: str | None = None
        self._http = httpx.Client(
            headers={
                "User-Agent": _UA_OKHTTP,
                "Content-Type": "application/json; charset=utf-8",
                "Accept-Encoding": "gzip",
            },
            timeout=30.0,
        )

    # ------------------------------------------------------------------
    # Credentials
    # ------------------------------------------------------------------

    def _get_credentials(self) -> tuple[str, str, str]:
        login_id = os.getenv("INBODY_LOGIN_ID")
        password = os.getenv("INBODY_LOGIN_PW")
        country = os.getenv("INBODY_COUNTRY_CODE", "US")
        if not login_id or not password:
            raise InBodyError(
                "INBODY_LOGIN_ID and INBODY_LOGIN_PW env vars must be set"
            )
        return login_id, password, country

    def _envelope(self, country: str, **extra: object) -> dict:
        """Build a request body merging the app envelope, country code, and extras.

        `country` here is the numeric phone code (CountryCode) the API bodies
        expect, e.g. "1" for the US.
        """
        return {**_APP_ENVELOPE, "CountryCode": country, **extra}

    # ------------------------------------------------------------------
    # Host resolution
    # ------------------------------------------------------------------

    def _resolve_api_base(self) -> str:
        """Resolve the regional API host for the configured country.

        Uses POST /CommonAPI/GetCountryInfoV2 and picks the Type=="API" row whose
        ISO Code2 matches INBODY_COUNTRY_CODE (e.g. "US"). The numeric phone code
        (Number) from that row is cached for use in request bodies. Cached after
        the first call.

        Note: the numeric phone code is ambiguous (US, CA, PR all share "1"), so
        we deliberately key host selection on the ISO Code2 rather than Number.
        """
        if self._api_base is not None:
            return self._api_base

        _, _, country = self._get_credentials()
        payload = self._envelope("", SyncDatetime=_FULL_SYNC_DATETIME)
        resp = self._http.post(f"{COMMON_URL}/CommonAPI/GetCountryInfoV2", json=payload)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("IsSuccess"):
            raise InBodyError(f"GetCountryInfoV2 failed: {data.get('ErrorMsg')}")

        rows = data.get("Data", []) or []
        api_row = next(
            (
                r
                for r in rows
                if r.get("Type") == "API"
                and str(r.get("Code2")).upper() == country.upper()
            ),
            None,
        )
        if api_row is None:
            raise InBodyError(
                f"No API host found for country {country!r} in GetCountryInfoV2. "
                "INBODY_COUNTRY_CODE should be an ISO Code2 such as 'US'."
            )

        # The version segment (e.g. "/V2") is already part of the per-endpoint
        # paths we send, so we keep only the bare domain as the base.
        self._api_base = api_row["Domain"].rstrip("/")
        self._country_number = str(api_row.get("Number"))
        logger.info(
            "Resolved InBody API base for %s (CountryCode=%s): %s",
            country,
            self._country_number,
            self._api_base,
        )
        return self._api_base

    def _country_code(self) -> str:
        """Numeric phone code (CountryCode) for request bodies, e.g. '1' for US."""
        self._resolve_api_base()
        assert self._country_number is not None
        return self._country_number

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def login(self) -> None:
        """Authenticate with InBody and cache the JWT + UID."""
        login_id, password, _ = self._get_credentials()
        base = self._resolve_api_base()
        country = self._country_code()

        payload = self._envelope(
            country,
            AuthProvider="",
            AuthProviderID="",
            AuthToken="",
            CustomKey="",
            DeviceType="Pixel 6 Pro 14",
            LoginID=login_id,
            LoginPW=password,
            Type="Login",
            SyncDatetime=_FULL_SYNC_DATETIME,
            SyncDatetimeBasalMedical=_FULL_SYNC_DATETIME,
            SyncDatetimeCardiac=_FULL_SYNC_DATETIME,
            SyncDatetimeExercise=_FULL_SYNC_DATETIME,
            SyncDatetimeInBody=_FULL_SYNC_DATETIME,
            SyncDatetimeNutrition=_FULL_SYNC_DATETIME,
            SyncDatetimeSleep=_FULL_SYNC_DATETIME,
            SyncType="Main;InBody;Exercise;Nutrition;Sleep;EasyTrainning;BasalMedical;",
        )

        logger.info("Logging in to InBody as %s", login_id)
        resp = self._http.post(
            f"{base}/V2/Main/GetLoginWithSyncDataPartV2", json=payload
        )
        resp.raise_for_status()
        data = resp.json()

        if not data.get("IsSuccess") or not data.get("Token"):
            raise InBodyError(f"Login failed: {data.get('ErrorMsg') or data}")

        self._token = data["Token"]
        self._refresh_token = data.get("RefreshToken")
        self._uid = (data.get("Data") or {}).get("UID")
        if not self._uid:
            raise InBodyError("Login succeeded but no UID returned")
        logger.info(
            "InBody login successful (uid=%s, token=%s...)",
            self._uid,
            self._token[:12],
        )

    def _ensure_auth(self) -> None:
        if self._token is None:
            self.login()

    def _auth_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "User-Agent": _UA_DALVIK,
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json",
        }

    @property
    def uid(self) -> str:
        self._ensure_auth()
        assert self._uid is not None
        return self._uid

    # ------------------------------------------------------------------
    # Request helper
    # ------------------------------------------------------------------

    def _post(self, path: str, payload: dict, *, _retried: bool = False) -> dict:
        """Authenticated POST to the regional API; unwraps the response envelope.

        Re-authenticates once on HTTP 401/403 or a body-level auth failure.
        """
        self._ensure_auth()
        base = self._resolve_api_base()

        resp = self._http.post(
            f"{base}{path}", json=payload, headers=self._auth_headers()
        )

        if resp.status_code in (401, 403) and not _retried:
            logger.warning(
                "InBody auth rejected (%d), re-authenticating", resp.status_code
            )
            self._token = None
            self.login()
            return self._post(path, payload, _retried=True)

        resp.raise_for_status()
        data = resp.json()

        if isinstance(data, dict) and not data.get("IsSuccess"):
            err = (data.get("ErrorMsg") or "").lower()
            if not _retried and ("token" in err or "auth" in err or "expire" in err):
                logger.warning("InBody request rejected (%s), re-authenticating", err)
                self._token = None
                self.login()
                return self._post(path, payload, _retried=True)
            raise InBodyError(f"InBody API error: {data.get('ErrorMsg') or data}")

        return data

    # ------------------------------------------------------------------
    # Read-only data methods
    # ------------------------------------------------------------------

    def get_user_info(self) -> dict:
        """Fetch the user profile (height, weight, age, gender, email, ...)."""
        country = self._country_code()
        payload = self._envelope(
            country,
            uid=self.uid,
            syncDatetime=_FULL_SYNC_DATETIME,
            NumberPerData="100",
            CurrentIndex="0",
            Language="en-US",
            UseInBodyHomeDevice="false",
        )
        data = self._post("/V2/Main/GetUserInfo", payload)
        records = data.get("Data") or []
        if not records:
            raise InBodyError("GetUserInfo returned no profile data")
        return records[0]

    def get_scan_count(self) -> int:
        """Return the total number of InBody scans available."""
        country = self._country_code()
        payload = self._envelope(
            country,
            UID=self.uid,
            SyncDatetimeInBody=_FULL_SYNC_DATETIME,
            UseInBodyHomeDevice="false",
        )
        data = self._post("/V2/InBody/GetInBodyDataTotalCount", payload)
        return int((data.get("Data") or {}).get("InBodyDataCount", 0))

    def get_scans(self, number: int = 20, index: int = 0) -> list[dict]:
        """Fetch raw InBody scan records (paginated).

        Each record contains DATETIMES plus nested BCA (body composition
        analysis), MFA (BMI/%fat/SMM with norm ranges), and IMP (raw impedance)
        blocks.

        Args:
            number: Max records to return (NumberPerData).
            index: Pagination offset (CurrentIndex).
        """
        country = self._country_code()
        payload = self._envelope(
            country,
            uid=self.uid,
            syncDatetime=_FULL_SYNC_DATETIME,
            NumberPerData=str(number),
            CurrentIndex=str(index),
            Language="en-US",
            UseInBodyHomeDevice="false",
        )
        data = self._post("/V2/InBody/GetInBodyData", payload)
        return data.get("Data") or []
