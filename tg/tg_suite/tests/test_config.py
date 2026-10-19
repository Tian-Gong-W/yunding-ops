import os
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config import get_api_credentials, get_session_path, validate_session_name


class ConfigTests(unittest.TestCase):
    def test_api_credentials_are_read_from_environment(self) -> None:
        with patch.dict(
            os.environ,
            {"TG_API_ID": "12345", "TG_API_HASH": "0123456789abcdef"},
            clear=False,
        ):
            self.assertEqual(get_api_credentials(), (12345, "0123456789abcdef"))

    def test_missing_api_credentials_fail_fast(self) -> None:
        with patch.dict(os.environ, {"TG_API_ID": "", "TG_API_HASH": ""}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "TG_API_ID"):
                get_api_credentials()

    def test_session_name_rejects_path_traversal(self) -> None:
        for value in ("../escape", "a/b", "a\\b", "", ".", ".."):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_session_name(value)

    def test_session_path_stays_below_base_directory(self) -> None:
        base = Path("/tmp/tg-suite-tests/sessions")
        result = get_session_path(base, "account_01")
        self.assertEqual(result, base.resolve() / "account_01")


if __name__ == "__main__":
    unittest.main()
