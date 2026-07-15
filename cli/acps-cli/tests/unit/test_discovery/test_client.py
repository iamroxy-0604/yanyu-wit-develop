"""Basic tests for discovery client."""

from unittest.mock import MagicMock, patch

import pytest


def test_import():
    from acps_cli.discovery import client

    assert hasattr(client, "trigger_sync")
    assert hasattr(client, "query")


def test_trigger_sync_calls_gateway_endpoints_in_order() -> None:
    from acps_cli.discovery.client import trigger_sync

    with patch("httpx.request") as mock_request:
        hard_reset_response = MagicMock()
        hard_reset_response.status_code = 200
        hard_reset_response.text = "{}"

        sync_response = MagicMock()
        sync_response.status_code = 200
        sync_response.text = "{}"

        status_response = MagicMock()
        status_response.status_code = 200
        status_response.text = '{"object_count_by_type": {"acs": 1}}'

        mock_request.side_effect = [
            hard_reset_response,
            sync_response,
            status_response,
        ]

        status_payload = trigger_sync("http://localhost:9000")

    assert mock_request.call_count == 3
    assert mock_request.call_args_list[0].kwargs["timeout"] == 30
    assert mock_request.call_args_list[1].kwargs["timeout"] == 180
    assert mock_request.call_args_list[2].kwargs["timeout"] == 30
    assert status_payload["object_count_by_type"]["acs"] == 1


def test_trigger_sync_without_hard_reset_skips_reset_call() -> None:
    from acps_cli.discovery.client import trigger_sync

    with patch("httpx.request") as mock_request:
        sync_response = MagicMock()
        sync_response.status_code = 200
        sync_response.text = '{"success": true}'

        status_response = MagicMock()
        status_response.status_code = 200
        status_response.text = '{"object_count_by_type": {"acs": 0}}'

        mock_request.side_effect = [sync_response, status_response]

        status_payload = trigger_sync("http://localhost:9000", hard_reset=False, min_acs_count=0)

    assert mock_request.call_count == 2
    assert mock_request.call_args_list[0].args[1].endswith("/admin/dsp/sync")
    assert mock_request.call_args_list[1].args[1].endswith("/admin/dsp/status")
    assert status_payload["object_count_by_type"]["acs"] == 0


def test_trigger_sync_raises_gateway_error() -> None:
    from acps_cli.discovery.client import DiscoveryError, trigger_sync

    with patch("httpx.request") as mock_request:
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_request.return_value = mock_response
        with pytest.raises(DiscoveryError, match="DSP hard-reset 失败"):
            trigger_sync("http://localhost:9000")


def test_get_dsp_status_applies_min_acs_count() -> None:
    from acps_cli.discovery.client import DiscoveryError, get_dsp_status

    with patch("httpx.request") as mock_request:
        status_response = MagicMock()
        status_response.status_code = 200
        status_response.text = '{"object_count_by_type": {"acs": 1}}'
        mock_request.return_value = status_response

        with pytest.raises(DiscoveryError, match="ACS 对象不足"):
            get_dsp_status("http://localhost:9000", min_acs_count=2)


def test_register_webhook_returns_json_payload() -> None:
    from acps_cli.discovery.client import register_webhook

    with patch("httpx.request") as mock_request:
        response = MagicMock()
        response.status_code = 200
        response.text = '{"id": "wh-001", "status": "active"}'
        mock_request.return_value = response

        payload = register_webhook(
            "http://localhost:9000",
            {
                "url": "http://localhost:9015/admin/dsp/webhooks/receive",
                "secret": "shared-secret",
                "types": ["acs"],
                "events": ["data_change"],
            },
            headers={"Authorization": "Bearer admin-token"},
        )

    assert payload["id"] == "wh-001"
    assert mock_request.call_args.kwargs["headers"]["Authorization"] == "Bearer admin-token"


def test_discovery_error_is_exception():
    from acps_cli.discovery.client import DiscoveryError

    err = DiscoveryError("test error")
    assert str(err) == "test error"
    assert isinstance(err, Exception)
