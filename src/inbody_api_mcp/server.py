"""MCP server for InBody body-composition data via the mobile REST API."""

import json
import logging
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from .client import InBodyClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP(
    "inbody",
    instructions=(
        "InBody MCP server for body-composition data via the mobile REST API. "
        "Provides read-only access to InBody scans (body fat, muscle mass, body "
        "water, BMI, segmental impedance) and the user profile. Use get_profile "
        "for height/weight/age context, list_scans for a chronological summary, "
        "and get_scan for the full metrics of a single measurement."
    ),
)

_client: InBodyClient | None = None


def _get_client() -> InBodyClient:
    global _client
    if _client is None:
        _client = InBodyClient()
    return _client


def _ok(data: dict) -> str:
    return json.dumps({"status": "success", **data}, indent=2)


def _err(e: Exception) -> str:
    import httpx

    if isinstance(e, httpx.HTTPStatusError):
        status = e.response.status_code
        if status in (401, 403):
            msg = "Authentication failed. InBody session may have expired -- try again."
        elif status == 429:
            msg = "Rate limit exceeded. Wait a few minutes before retrying."
        elif status == 404:
            msg = f"Resource not found (HTTP {status})."
        else:
            msg = f"InBody API error (HTTP {status})."
    elif isinstance(e, httpx.TimeoutException):
        msg = "Request timed out. InBody may be slow -- try again."
    elif isinstance(e, httpx.ConnectError):
        msg = "Could not connect to InBody. Check network connectivity."
    else:
        msg = f"{type(e).__name__}: {e}"

    return json.dumps({"status": "error", "message": msg})


def _fmt_datetime(raw: str | None) -> str | None:
    """Format an InBody DATETIMES string (YYYYMMDDHHMMSS) as ISO 8601."""
    if not raw or len(raw) < 14:
        return raw
    try:
        return datetime.strptime(raw[:14], "%Y%m%d%H%M%S").isoformat()
    except ValueError:
        return raw


def _scan_summary(record: dict) -> dict:
    """Slim a raw scan record down to the most useful headline metrics."""
    bca = record.get("BCA") or {}
    mfa = record.get("MFA") or {}
    return {
        "datetime": _fmt_datetime(record.get("DATETIMES")),
        "raw_datetime": record.get("DATETIMES"),
        "weight_kg": bca.get("WT"),
        "bmi": mfa.get("BMI"),
        "percent_body_fat": mfa.get("PBF"),
        "skeletal_muscle_mass_kg": mfa.get("SMM"),
        "body_fat_mass_kg": bca.get("BFM"),
        "total_body_water_kg": bca.get("TBW"),
        "equipment": bca.get("EQUIP"),
    }


# ------------------------------------------------------------------
# Tools
# ------------------------------------------------------------------

_READONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}


@mcp.tool(annotations=_READONLY)
def get_profile() -> str:
    """Get the InBody user profile.

    Returns identity and baseline body metrics (name, gender, age, height,
    weight, email) useful for interpreting scan results.
    """
    try:
        client = _get_client()
        info = client.get_user_info()
        return _ok(
            {
                "profile": {
                    "uid": info.get("UID"),
                    "name": info.get("Name"),
                    "gender": info.get("Gender"),
                    "age": info.get("Age"),
                    "birthday": info.get("Birthday"),
                    "height_cm": info.get("Height"),
                    "weight_kg": info.get("Weight"),
                    "email": info.get("Email"),
                }
            }
        )
    except Exception as e:
        return _err(e)


@mcp.tool(annotations=_READONLY)
def get_scan_count() -> str:
    """Get the total number of InBody scans available for the account."""
    try:
        client = _get_client()
        count = client.get_scan_count()
        return _ok({"scan_count": count})
    except Exception as e:
        return _err(e)


@mcp.tool(annotations=_READONLY)
def list_scans(limit: int = 20, offset: int = 0) -> str:
    """List InBody scans with headline metrics, newest first.

    Returns a slim summary per scan (date, weight, BMI, %body fat, skeletal
    muscle mass, body fat mass, total body water, equipment). Use get_scan with
    a scan's raw_datetime for the full metric set.

    Args:
        limit: Maximum number of scans to return (default 20).
        offset: Pagination offset into the scan history (default 0).
    """
    try:
        client = _get_client()
        records = client.get_scans(number=limit, index=offset)
        scans = [_scan_summary(r) for r in records]
        return _ok({"count": len(scans), "scans": scans})
    except Exception as e:
        return _err(e)


@mcp.tool(annotations=_READONLY)
def get_scan(raw_datetime: str | None = None) -> str:
    """Get the full metric set for a single InBody scan.

    Returns the complete BCA (body composition), MFA (BMI/%fat/muscle with norm
    ranges), and IMP (segmental/multi-frequency impedance) blocks.

    Args:
        raw_datetime: The scan's raw DATETIMES value (YYYYMMDDHHMMSS) from
            list_scans. Defaults to the most recent scan.
    """
    try:
        client = _get_client()
        records = client.get_scans(number=100, index=0)
        if not records:
            return _ok({"scan": None, "note": "No scans found."})

        if raw_datetime is None:
            record = records[0]
        else:
            record = next(
                (r for r in records if r.get("DATETIMES") == raw_datetime), None
            )
            if record is None:
                return _err(
                    ValueError(
                        f"No scan found with raw_datetime {raw_datetime!r}. "
                        "Use list_scans to find valid values."
                    )
                )

        return _ok(
            {
                "scan": {
                    "datetime": _fmt_datetime(record.get("DATETIMES")),
                    "raw_datetime": record.get("DATETIMES"),
                    "summary": _scan_summary(record),
                    "BCA": record.get("BCA"),
                    "MFA": record.get("MFA"),
                    "IMP": record.get("IMP"),
                }
            }
        )
    except Exception as e:
        return _err(e)


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------


def main() -> None:
    """Run the MCP server over stdio.

    stdio is the only supported transport. The Docker image wraps this stdio
    process with supergateway to expose streamable-HTTP; local clients spawn it
    directly via uvx/uv.
    """
    # Load .env for local development. No-op if the file is missing.
    # override=False keeps real environment variables (Docker, systemd, MCP
    # client `env` blocks, etc.) authoritative over .env.
    from dotenv import find_dotenv, load_dotenv

    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path and load_dotenv(dotenv_path, override=False):
        logger.info("Loaded .env from %s", dotenv_path)

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
