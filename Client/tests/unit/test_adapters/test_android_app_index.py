import json
from pathlib import Path
from unittest.mock import Mock

from src.adapters.android_app_index import AndroidAppIndex, AndroidAppIndexData, AndroidAppIndexEntry
from src.config.apps import normalize_app_name


class TestAndroidAppIndex:
    def test_refresh_builds_index_and_persists_cache(self, tmp_path):
        adapter = Mock()
        adapter.device_id = "device-001"
        adapter._log = Mock()
        adapter._check_output = Mock(side_effect=[
            "package:com.android.notes\npackage:com.example.hidden\n",
            "com.android.notes/.MainActivity\n",
            "application-label:原子笔记\nnonLocalizedLabel=null\n",
            "com.android.notes/.MainActivity\n",
            "application-label:内部服务\n",
            "",
        ])

        index = AndroidAppIndex(adapter, cache_root=tmp_path, time_fn=lambda: 1234.0)
        refreshed = index.refresh()

        key = normalize_app_name("原子笔记")
        assert refreshed.resolve("原子笔记") == "com.android.notes"
        assert refreshed.key_to_packages[key] == ["com.android.notes"]
        assert "com.example.hidden" not in refreshed.package_meta

        payload = json.loads((tmp_path / "device-001.json").read_text(encoding="utf-8"))
        assert payload["device_id"] == "device-001"
        assert payload["generated_at"] == 1234.0
        assert payload["key_to_packages"][key] == ["com.android.notes"]

    def test_load_cached_uses_valid_disk_cache(self, tmp_path):
        adapter = Mock()
        adapter.device_id = "device-001"
        adapter._log = Mock()

        cache_path = tmp_path / "device-001.json"
        cache_path.write_text(json.dumps({
            "schema_version": 1,
            "device_id": "device-001",
            "generated_at": 1000.0,
            "key_to_packages": {normalize_app_name("原子笔记"): ["com.android.notes"]},
            "package_meta": {
                "com.android.notes": {
                    "package": "com.android.notes",
                    "labels": ["原子笔记"],
                    "launchable": True,
                }
            },
        }, ensure_ascii=False), encoding="utf-8")

        index = AndroidAppIndex(adapter, cache_root=tmp_path, ttl_seconds=86400, time_fn=lambda: 1001.0)
        loaded = index.load_cached()

        assert loaded is not None
        assert index.resolve("原子笔记") == "com.android.notes"

    def test_load_cached_ignores_corrupt_cache(self, tmp_path):
        adapter = Mock()
        adapter.device_id = "device-001"
        adapter._log = Mock()
        adapter._check_output = Mock(side_effect=[
            "package:com.android.notes\n",
            "application-label:原子笔记\n",
            "com.android.notes/.MainActivity\n",
        ])

        cache_path = tmp_path / "device-001.json"
        cache_path.write_text("{invalid json", encoding="utf-8")

        index = AndroidAppIndex(adapter, cache_root=tmp_path, time_fn=lambda: 2000.0)
        assert index.load_cached() is None

        refreshed = index.refresh()
        assert refreshed.resolve("原子笔记") == "com.android.notes"
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        assert payload["generated_at"] == 2000.0

    def test_resolve_returns_none_for_ambiguous_name(self):
        data = AndroidAppIndexData(
            key_to_packages={normalize_app_name("Notes"): ["pkg.one", "pkg.two"]},
            generated_at=100.0,
        )

        assert data.resolve("Notes") is None
        assert data.is_ambiguous("Notes") is True

    def test_invalidate_package_removes_cached_mapping(self, tmp_path):
        adapter = Mock()
        adapter.device_id = "device-001"
        adapter._log = Mock()

        cache_path = tmp_path / "device-001.json"
        cache_path.write_text(json.dumps({
            "schema_version": 1,
            "device_id": "device-001",
            "generated_at": 1000.0,
            "key_to_packages": {normalize_app_name("原子笔记"): ["com.android.notes"]},
            "package_meta": {
                "com.android.notes": {
                    "package": "com.android.notes",
                    "labels": ["原子笔记"],
                    "launchable": True,
                }
            },
        }, ensure_ascii=False), encoding="utf-8")

        index = AndroidAppIndex(adapter, cache_root=tmp_path, time_fn=lambda: 1001.0)
        index.load_cached()
        index.invalidate("com.android.notes")

        assert index.resolve("原子笔记") is None
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        assert payload["key_to_packages"] == {}
        assert payload["package_meta"] == {}

    def test_expired_cache_is_treated_as_miss(self, tmp_path):
        adapter = Mock()
        adapter.device_id = "device-001"
        adapter._log = Mock()

        cache_path = tmp_path / "device-001.json"
        cache_path.write_text(json.dumps({
            "schema_version": 1,
            "device_id": "device-001",
            "generated_at": 1000.0,
            "key_to_packages": {normalize_app_name("原子笔记"): ["com.android.notes"]},
            "package_meta": {
                "com.android.notes": {
                    "package": "com.android.notes",
                    "labels": ["原子笔记"],
                    "launchable": True,
                }
            },
        }, ensure_ascii=False), encoding="utf-8")

        index = AndroidAppIndex(adapter, cache_root=tmp_path, ttl_seconds=10, time_fn=lambda: 2000.0)
        assert index.load_cached() is None
        assert index.resolve("原子笔记") is None

    def test_normalization_matches_separator_variants(self, tmp_path):
        adapter = Mock()
        adapter.device_id = "device-001"
        adapter._log = Mock()
        adapter._check_output = Mock(side_effect=[
            "package:com.android.settings\n",
            "application-label:Android System Settings\n",
            "com.android.settings/.Settings\n",
        ])

        index = AndroidAppIndex(adapter, cache_root=tmp_path, time_fn=lambda: 10.0)
        index.refresh()

        assert index.resolve("Android-System-Settings") == "com.android.settings"
        assert index.resolve("android  system settings") == "com.android.settings"

    def test_refresh_includes_launcher_packages_missing_from_pm_list_packages_3(self, tmp_path):
        adapter = Mock()
        adapter.device_id = "device-001"
        adapter._log = Mock()
        adapter._check_output = Mock(side_effect=[
            "package:com.example.thirdparty\n",
            "com.android.notes/.Notes\n",
            "application-label:第三方应用\n",
            "com.example.thirdparty/.MainActivity\n",
            "",
            "com.android.notes/.Notes\n",
        ])

        index = AndroidAppIndex(adapter, cache_root=tmp_path, time_fn=lambda: 42.0)
        refreshed = index.refresh()

        assert refreshed.resolve("原子笔记") == "com.android.notes"
        assert "com.android.notes" in refreshed.package_meta
        assert refreshed.package_meta["com.android.notes"].labels == ["原子笔记"]

    def test_get_package_suggestions_returns_cached_labels(self, tmp_path):
        adapter = Mock()
        adapter.device_id = "device-001"
        adapter._log = Mock()

        index = AndroidAppIndex(adapter, cache_root=tmp_path, time_fn=lambda: 100.0)
        index._memory_index = AndroidAppIndexData(
            key_to_packages={normalize_app_name("原子笔记"): ["com.android.notes"]},
            package_meta={
                "com.android.notes": AndroidAppIndexEntry(
                    package="com.android.notes",
                    labels=["原子笔记", "Notes"],
                    launchable=True,
                )
            },
            generated_at=100.0,
        )

        suggestions = index.get_package_suggestions(limit=5)

        assert suggestions == [("com.android.notes", ["原子笔记", "Notes"])]
