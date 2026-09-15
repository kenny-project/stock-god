import os

from services.runners import build_command, classify_error


def test_build_download_command():
    cmd = build_command("download", "US.NKE", {"years": 5})
    assert os.path.basename(cmd[1]) == "sec_filings.py"
    assert cmd[-4:] == ["download", "US.NKE", "--years", "5"]


def test_build_analysis_command():
    cmd = build_command("analysis", "NKE", {})
    assert os.path.basename(cmd[1]) == "sec_analysis.py"
    assert cmd[-2:] == ["NKE", "--all"]


def test_build_dcf_command():
    cmd = build_command("dcf", "US.NKE", {"growth": 8, "discount": 10, "years": 5, "safety": 0.3})
    assert os.path.basename(cmd[1]) == "dcf.py"
    assert cmd[2] == "US.NKE"
    assert "--growth" in cmd and "8" in cmd


def test_build_unknown_type():
    import pytest
    with pytest.raises(ValueError):
        build_command("nope", "US.NKE", {})


def test_classify_rate_limit():
    code, summary = classify_error(1, "HTTPError 403 Forbidden\nrequest blocked by sec.gov")
    assert code == "SEC_RATE_LIMITED"
    assert summary


def test_classify_timeout():
    code, _ = classify_error(1, "urlopen error timed out")
    assert code == "NETWORK_TIMEOUT"


def test_classify_not_found():
    code, _ = classify_error(1, "HTTP Error 404: Not Found")
    assert code == "NO_FILINGS_FOUND"


def test_classify_unknown():
    code, _ = classify_error(2, "traceback ...")
    assert code == "SCRIPT_EXIT_NONZERO"
