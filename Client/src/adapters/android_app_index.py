"""Android app name to package index with per-device caching."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from src.config.apps import get_app_aliases, normalize_app_name

_DEFAULT_ANDROID_SYSTEM_LABELS = {
    "com.android.notes": ["原子笔记"],
}

if TYPE_CHECKING:
    from src.adapters.adb_adapter import ADBAdapter

CACHE_SCHEMA_VERSION = 1
CACHE_TTL_SECONDS = 24 * 60 * 60
_PACKAGE_PREFIX = "package:"
_APPLICATION_LABEL_RE = re.compile(r"application-label(?:-[^:=\s]+)?:(.+)")
_NON_LOCALIZED_LABEL_RE = re.compile(r"nonLocalizedLabel=(.+)")
_RESOLVE_ACTIVITY_PACKAGE_RE = re.compile(r"packageName=([A-Za-z0-9._]+)")


@dataclass
class AndroidAppIndexEntry:
    package: str
    labels: list[str] = field(default_factory=list)
    launchable: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "labels": list(self.labels),
            "launchable": self.launchable,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AndroidAppIndexEntry":
        return cls(
            package=str(payload.get("package", "")).strip(),
            labels=[str(label) for label in payload.get("labels", []) if str(label).strip()],
            launchable=payload.get("launchable"),
        )


@dataclass
class AndroidAppIndexData:
    key_to_packages: dict[str, list[str]] = field(default_factory=dict)
    package_meta: dict[str, AndroidAppIndexEntry] = field(default_factory=dict)
    generated_at: float = 0.0

    def resolve(self, app_name: str) -> str | None:
        key = normalize_app_name(app_name)
        if not key:
            return None
        packages = self.key_to_packages.get(key, [])
        if len(packages) == 1:
            return packages[0]
        return None

    def is_ambiguous(self, app_name: str) -> bool:
        key = normalize_app_name(app_name)
        if not key:
            return False
        return len(self.key_to_packages.get(key, [])) > 1

    def invalidate_package(self, package: str) -> bool:
        removed = False
        for key, packages in list(self.key_to_packages.items()):
            filtered = [candidate for candidate in packages if candidate != package]
            if len(filtered) != len(packages):
                removed = True
                if filtered:
                    self.key_to_packages[key] = filtered
                else:
                    del self.key_to_packages[key]
        if package in self.package_meta:
            removed = True
            del self.package_meta[package]
        return removed

    def to_payload(self, device_id: str) -> dict[str, Any]:
        return {
            "schema_version": CACHE_SCHEMA_VERSION,
            "device_id": device_id,
            "generated_at": self.generated_at,
            "key_to_packages": self.key_to_packages,
            "package_meta": {
                package: entry.to_dict()
                for package, entry in self.package_meta.items()
            },
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any], device_id: str) -> "AndroidAppIndexData":
        if payload.get("schema_version") != CACHE_SCHEMA_VERSION:
            raise ValueError("Unsupported cache schema version")
        cached_device_id = str(payload.get("device_id", "")).strip()
        if cached_device_id and cached_device_id != device_id:
            raise ValueError("Cache device id mismatch")

        raw_key_to_packages = payload.get("key_to_packages")
        if not isinstance(raw_key_to_packages, dict):
            raise ValueError("Invalid key_to_packages")

        key_to_packages: dict[str, list[str]] = {}
        for raw_key, raw_packages in raw_key_to_packages.items():
            key = str(raw_key).strip()
            if not key or not isinstance(raw_packages, list):
                continue
            packages = [str(package).strip() for package in raw_packages if str(package).strip()]
            if packages:
                key_to_packages[key] = packages

        raw_package_meta = payload.get("package_meta")
        if not isinstance(raw_package_meta, dict):
            raise ValueError("Invalid package_meta")

        package_meta: dict[str, AndroidAppIndexEntry] = {}
        for package, raw_entry in raw_package_meta.items():
            if not isinstance(raw_entry, dict):
                continue
            entry = AndroidAppIndexEntry.from_dict(raw_entry)
            if entry.package:
                package_meta[str(package).strip() or entry.package] = entry

        generated_at = payload.get("generated_at", 0)
        if not isinstance(generated_at, (int, float)):
            raise ValueError("Invalid generated_at")

        return cls(
            key_to_packages=key_to_packages,
            package_meta=package_meta,
            generated_at=float(generated_at),
        )


class AndroidAppIndex:
    """Android-only dynamic app resolver backed by ADB shell metadata."""

    _LAUNCHER_QUERY_COMMAND = [
        "shell",
        "cmd",
        "package",
        "query-activities",
        "--brief",
        "-a",
        "android.intent.action.MAIN",
        "-c",
        "android.intent.category.LAUNCHER",
    ]

    def __init__(
        self,
        adapter: "ADBAdapter",
        cache_root: str | Path | None = None,
        ttl_seconds: int = CACHE_TTL_SECONDS,
        time_fn: Callable[[], float] | None = None,
    ) -> None:
        self._adapter = adapter
        self._ttl_seconds = ttl_seconds
        self._time_fn = time_fn or time.time
        default_cache_root = Path(__file__).resolve().parents[2] / "cache" / "android_apps"
        self._cache_root = Path(cache_root) if cache_root is not None else default_cache_root
        self._memory_index: AndroidAppIndexData | None = None
        self._disk_cache_loaded = False

    @property
    def cache_path(self) -> Path:
        safe_device_id = self._adapter.device_id or "unknown-device"
        return self._cache_root / f"{safe_device_id}.json"

    def resolve(self, app_name: str) -> str | None:
        index = self._get_fresh_memory_index()
        if index is None:
            return None
        package = index.resolve(app_name)
        if package or not index.is_ambiguous(app_name):
            return package
        self._adapter._log(
            "warning",
            f"[android_app_index] Ambiguous dynamic app name for device {self._adapter.device_id}: {app_name}",
        )
        return None

    def load_cached(self) -> AndroidAppIndexData | None:
        if self._disk_cache_loaded:
            return self._get_fresh_memory_index()

        self._disk_cache_loaded = True
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            index = AndroidAppIndexData.from_payload(payload, self._adapter.device_id)
        except FileNotFoundError:
            return None
        except Exception as exc:
            self._adapter._log(
                "warning",
                f"[android_app_index] Ignoring invalid cache for device {self._adapter.device_id}: {exc}",
            )
            self._memory_index = None
            return None

        if self._is_expired(index):
            self._adapter._log(
                "debug",
                f"[android_app_index] Cached app index expired for device {self._adapter.device_id}",
            )
            self._memory_index = None
            return None

        self._memory_index = index
        return index

    def refresh(self) -> AndroidAppIndexData:
        index = self._build_index()
        self._memory_index = index
        self._disk_cache_loaded = True
        self._persist(index)
        return index

    def invalidate(self, package: str | None = None) -> None:
        if package:
            removed = False
            if self._memory_index is not None:
                removed = self._memory_index.invalidate_package(package) or removed
            cached = self._load_index_from_disk(ignore_ttl=True)
            if cached is not None and cached.invalidate_package(package):
                removed = True
                self._persist(cached)
                if self._memory_index is None or self._memory_index.generated_at <= cached.generated_at:
                    self._memory_index = cached
            if removed:
                self._adapter._log(
                    "debug",
                    f"[android_app_index] Invalidated package {package} for device {self._adapter.device_id}",
                )
            else:
                self._memory_index = None
                self._disk_cache_loaded = False
            return

        self._memory_index = None
        self._disk_cache_loaded = False

    def _get_fresh_memory_index(self) -> AndroidAppIndexData | None:
        if self._memory_index is None:
            return None
        if self._is_expired(self._memory_index):
            self._memory_index = None
            return None
        return self._memory_index

    def _is_expired(self, index: AndroidAppIndexData) -> bool:
        if index.generated_at <= 0:
            return True
        return (self._time_fn() - index.generated_at) > self._ttl_seconds

    def _load_index_from_disk(self, ignore_ttl: bool = False) -> AndroidAppIndexData | None:
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            index = AndroidAppIndexData.from_payload(payload, self._adapter.device_id)
        except Exception:
            return None

        if not ignore_ttl and self._is_expired(index):
            return None
        return index

    def _persist(self, index: AndroidAppIndexData) -> None:
        self._cache_root.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(index.to_payload(self._adapter.device_id), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _build_index(self) -> AndroidAppIndexData:
        key_to_packages: dict[str, list[str]] = {}
        package_meta: dict[str, AndroidAppIndexEntry] = {}

        for package in self._list_candidate_packages():
            labels = self._extract_labels(package)
            if not labels:
                continue
            launchable = self._is_package_launchable(package)
            if launchable is False:
                continue
            package_meta[package] = AndroidAppIndexEntry(
                package=package,
                labels=labels,
                launchable=launchable,
            )
            for label in labels:
                key = normalize_app_name(label)
                if not key:
                    continue
                packages = key_to_packages.setdefault(key, [])
                if package not in packages:
                    packages.append(package)

        return AndroidAppIndexData(
            key_to_packages=key_to_packages,
            package_meta=package_meta,
            generated_at=self._time_fn(),
        )

    def _list_candidate_packages(self) -> list[str]:
        try:
            output = self._adapter._check_output(
                ["shell", "pm", "list", "packages", "-3"],
                encoding="utf-8",
            )
        except Exception as exc:
            self._adapter._log(
                "warning",
                f"[android_app_index] Failed to list installed packages for device {self._adapter.device_id}: {exc}",
            )
            return []

        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="ignore")

        packages: list[str] = []
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line.startswith(_PACKAGE_PREFIX):
                continue
            package = line[len(_PACKAGE_PREFIX):].strip()
            if package:
                packages.append(package)
        launcher_packages = self._list_launcher_packages()
        for package in launcher_packages:
            if package not in packages:
                packages.append(package)
        return packages

    def _list_launcher_packages(self) -> list[str]:
        try:
            output = self._adapter._check_output(self._LAUNCHER_QUERY_COMMAND, encoding="utf-8")
        except Exception as exc:
            self._adapter._log(
                "debug",
                f"[android_app_index] Failed to query launcher activities for device {self._adapter.device_id}: {exc}",
            )
            return []

        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="ignore")

        packages: list[str] = []
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line or "/" not in line or line.startswith(("Activity #", "priority=", "100 activities found")):
                continue
            package = line.split("/", 1)[0].strip()
            if package and package not in packages:
                packages.append(package)
        return packages

    def _extract_labels(self, package: str) -> list[str]:
        try:
            output = self._adapter._check_output(
                ["shell", "dumpsys", "package", package],
                encoding="utf-8",
            )
        except Exception as exc:
            self._adapter._log(
                "warning",
                f"[android_app_index] Failed to inspect package {package}: {exc}",
            )
            return []

        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="ignore")

        labels: list[str] = []
        for raw_line in output.splitlines():
            line = raw_line.strip()
            label_match = _APPLICATION_LABEL_RE.search(line)
            if label_match:
                label = label_match.group(1).strip().strip('"')
                if label and label not in labels:
                    labels.append(label)
                continue
            non_localized_match = _NON_LOCALIZED_LABEL_RE.search(line)
            if non_localized_match:
                label = non_localized_match.group(1).strip().strip('"')
                if label and label != "null" and label not in labels:
                    labels.append(label)

        if labels:
            return labels

        for alias in get_app_aliases(package):
            if alias not in labels:
                labels.append(alias)
        for fallback_label in _DEFAULT_ANDROID_SYSTEM_LABELS.get(package, []):
            if fallback_label not in labels:
                labels.append(fallback_label)
        return labels

    def get_package_suggestions(self, limit: int = 5) -> list[tuple[str, list[str]]]:
        index = self._get_fresh_memory_index()
        if index is None:
            return []

        suggestions: list[tuple[str, list[str]]] = []
        for package, entry in index.package_meta.items():
            labels = [label for label in entry.labels if label]
            if not labels:
                continue
            suggestions.append((package, labels))
            if len(suggestions) >= limit:
                break
        return suggestions

    def _is_package_launchable(self, package: str) -> bool | None:
        commands = [
            [
                "shell",
                "cmd",
                "package",
                "resolve-activity",
                "--brief",
                "-c",
                "android.intent.category.LAUNCHER",
                package,
            ],
            [
                "shell",
                "cmd",
                "package",
                "resolve-activity",
                "--brief",
                package,
            ],
        ]
        for command in commands:
            try:
                output = self._adapter._check_output(command, encoding="utf-8")
            except Exception:
                continue
            if isinstance(output, bytes):
                output = output.decode("utf-8", errors="ignore")
            if package in output or _RESOLVE_ACTIVITY_PACKAGE_RE.search(output):
                return True
            return False
        return None
