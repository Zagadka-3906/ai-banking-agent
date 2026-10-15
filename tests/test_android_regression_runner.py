import pytest

from scripts.run_android_regression import verify_results


def test_device_verifier_rejects_skips_and_duplicate_names(tmp_path):
    result = tmp_path / "TEST-suite.xml"
    result.write_text('<testsuite><testcase classname="A" name="x"><skipped/></testcase></testsuite>')
    with pytest.raises(ValueError):
        verify_results(tmp_path, 1)
    result.write_text('<testsuite><testcase classname="A" name="x"/><testcase classname="A" name="x"/></testsuite>')
    with pytest.raises(ValueError):
        verify_results(tmp_path, 2)
    result.write_text('<testsuite><testcase classname="A" name="x"/></testsuite>')
    assert verify_results(tmp_path, 1)["tests"] == 1
    result.write_text('<testsuite tests="1" errors="1"><testcase classname="A" name="x"/></testsuite>')
    with pytest.raises(ValueError):
        verify_results(tmp_path, 1)
