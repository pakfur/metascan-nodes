from client.errors import ApiError, OfflineError


def test_api_error_carries_status_and_body():
    e = ApiError(status_code=404, body_excerpt="not found")
    assert e.status_code == 404
    assert e.body_excerpt == "not found"
    assert "404" in str(e)
    assert "not found" in str(e)


def test_api_error_truncates_long_body():
    long_body = "x" * 2000
    e = ApiError(status_code=500, body_excerpt=long_body)
    # Body is truncated to 500 chars before being stored.
    assert len(e.body_excerpt) == 500


def test_offline_error_carries_reason():
    e = OfflineError(reason="connection refused")
    assert e.reason == "connection refused"
    assert "connection refused" in str(e)


def test_errors_are_distinct_exception_subclasses():
    assert issubclass(ApiError, Exception)
    assert issubclass(OfflineError, Exception)
    assert not issubclass(ApiError, OfflineError)
    assert not issubclass(OfflineError, ApiError)
