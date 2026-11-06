"""The rt-analytics client gives up on a hung API instead of waiting forever."""

from unittest.mock import patch

from tufts_local.analytics_utils import RT_ANALYTICS_TIMEOUT, get_ncq_eligibility


@patch("tufts_local.analytics_utils.requests.get")
def test_each_eligibility_request_has_a_timeout(mock_get, monkeypatch):
    monkeypatch.setenv("RT_ANALYTICS_BASE_URL", "https://analytics.example/")
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {"results": [{"username": "jdoe01"}], "total_count": 1}

    assert get_ncq_eligibility() == [{"username": "jdoe01"}]

    assert mock_get.call_args.kwargs["timeout"] == RT_ANALYTICS_TIMEOUT
