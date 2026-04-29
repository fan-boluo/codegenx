from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_SERVICE_ROOT = BACKEND_ROOT / "services" / "app-service"
APP_SERVICE_SERVICES_ROOT = APP_SERVICE_ROOT / "services"
for candidate in (str(APP_SERVICE_SERVICES_ROOT), str(APP_SERVICE_ROOT), str(BACKEND_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)


from shared.exceptions.business_exception import BusinessException
from static_service import StaticResourceService


class StaticResourceServiceContractTest(unittest.TestCase):
    def test_resolve_resource_falls_back_to_single_html_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir) / "aWquOw"
            base_dir.mkdir()
            html_file = base_dir / "recipe_collection.html"
            html_file.write_text("<html><body>ok</body></html>", encoding="utf-8")

            file_path, media_type = StaticResourceService()._resolve_file(base_dir, "", not_found_message="missing")

        self.assertEqual(file_path.name, "recipe_collection.html")
        self.assertEqual(media_type, "text/html; charset=utf-8")

    def test_resolve_resource_prefers_index_html_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir) / "preview"
            base_dir.mkdir()
            (base_dir / "index.html").write_text("<html>index</html>", encoding="utf-8")
            (base_dir / "other.html").write_text("<html>other</html>", encoding="utf-8")

            file_path, _ = StaticResourceService()._resolve_file(base_dir, "", not_found_message="missing")

        self.assertEqual(file_path.name, "index.html")

    def test_resolve_resource_rejects_ambiguous_html_entries_without_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir) / "preview"
            base_dir.mkdir()
            (base_dir / "a.html").write_text("<html>a</html>", encoding="utf-8")
            (base_dir / "b.html").write_text("<html>b</html>", encoding="utf-8")

            with self.assertRaises(BusinessException):
                StaticResourceService()._resolve_file(base_dir, "", not_found_message="missing")


if __name__ == "__main__":
    unittest.main()