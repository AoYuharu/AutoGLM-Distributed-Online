"""
截图管理器测试
"""
import os
import tempfile
import pytest
from pathlib import Path
from unittest.mock import Mock, patch

from src.screenshot.manager import (
    ScreenshotManager,
    ScreenshotConfig,
    ScreenshotMode,
)


class TestScreenshotConfig:
    """截图配置测试"""

    def test_default_config(self):
        """测试默认配置"""
        config = ScreenshotConfig()
        assert config.mode == ScreenshotMode.HYBRID
        assert config.local_path == "./screenshots"
        assert "error" in config.upload_on
        assert config.interval == 5

    def test_should_upload_on_error(self):
        """测试错误时上传"""
        config = ScreenshotConfig()
        assert config.should_upload_on_step(0, "error") is True

    def test_should_upload_on_finish(self):
        """测试完成时上传"""
        config = ScreenshotConfig()
        assert config.should_upload_on_step(0, "finish") is True

    def test_should_upload_on_interval(self):
        """测试间隔上传"""
        config = ScreenshotConfig(interval=5, upload_on=["error", "finish", "interval:5"])
        assert config.should_upload_on_step(5, "running") is True
        assert config.should_upload_on_step(10, "running") is True
        assert config.should_upload_on_step(3, "running") is False

    def test_should_not_upload_on_running(self):
        """测试普通状态不上传"""
        config = ScreenshotConfig(upload_on=["error", "finish"])
        assert config.should_upload_on_step(0, "running") is False


class TestScreenshotManager:
    """截图管理器测试"""

    @pytest.fixture
    def temp_dir(self):
        """创建临时目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def config(self, temp_dir):
        """创建测试配置"""
        return ScreenshotConfig(
            mode=ScreenshotMode.LOCAL,
            local_path=temp_dir,
        )

    @pytest.fixture
    def manager(self, config):
        """创建管理器"""
        return ScreenshotManager(config)

    def test_save_screenshot(self, manager, temp_dir):
        """测试保存截图"""
        data = b"fake image data"
        path = manager.save(data, "task_001", 0)

        assert path is not None
        assert Path(path).exists()
        assert Path(path).read_bytes() == data

    def test_save_screenshot_with_subdirectory(self, manager, temp_dir):
        """测试按任务ID创建子目录"""
        data = b"fake image data"

        # 保存多个截图
        manager.save(data, "task_001", 0)
        manager.save(data, "task_001", 1)
        manager.save(data, "task_002", 0)

        # 验证目录结构
        task001_dir = Path(temp_dir) / "task_001"
        task002_dir = Path(temp_dir) / "task_002"

        assert task001_dir.exists()
        assert task002_dir.exists()
        assert len(list(task001_dir.glob("*.png"))) == 2

    def test_save_final_screenshot(self, manager, temp_dir):
        """测试保存最终截图"""
        data = b"final screenshot"
        path = manager.save_final(data, "task_001")

        assert path is not None
        assert Path(path).exists()
        assert Path(path).name == "final.png"

    def test_get_screenshot_urls(self, manager, temp_dir):
        """测试获取截图列表"""
        # 保存一些截图
        for i in range(5):
            path = manager.save(b"data", "task_001", i)
            assert path is not None

        urls = manager.get_screenshot_urls("task_001")

        assert len(urls) == 5
        assert urls[0]["step"] == 0
        assert urls[4]["step"] == 4

    def test_get_screenshot_urls_with_range(self, manager, temp_dir):
        """测试获取指定范围的截图"""
        for i in range(10):
            manager.save(b"data", "task_001", i)

        urls = manager.get_screenshot_urls("task_001", start_step=3, end_step=6)

        assert len(urls) == 4
        assert all(3 <= u["step"] <= 6 for u in urls)

    def test_get_storage_size(self, manager, temp_dir):
        """测试获取存储大小"""
        data = b"x" * 1000
        manager.save(data, "task_001", 0)
        manager.save(data, "task_001", 1)

        size = manager.get_storage_size()
        assert size >= 2000

    def test_cleanup_old(self, manager, temp_dir):
        """测试清理过期截图"""
        import time

        data = b"old data"

        # 创建一个旧文件
        path = manager.save(data, "task_old", 0)
        assert path is not None

        # 手动修改时间为1天前
        old_time = Path(path).stat().st_mtime - 86400 * 8
        Path(path).touch()
        os.utime(path, (old_time, old_time))

        # 清理7天前的文件
        cleaned = manager.cleanup_old(days=7)

        assert cleaned >= 1
        assert not Path(path).exists()

    def test_upload_function(self, temp_dir):
        """测试上传功能"""
        config = ScreenshotConfig(
            mode=ScreenshotMode.HYBRID,
            local_path=temp_dir,
        )

        # 模拟上传函数
        upload_func = Mock(return_value="http://server/screenshots/task_001/step_0.png")
        manager = ScreenshotManager(config, upload_func=upload_func)

        # 保存并上传
        data = b"test data"
        path = manager.save(data, "task_001", 0)
        assert path is not None

        url = manager.upload(path, "task_001", 0)
        assert url == "http://server/screenshots/task_001/step_0.png"
        upload_func.assert_called_once()

    def test_close(self, config):
        """测试关闭管理器"""
        manager = ScreenshotManager(config)
        manager.close()  # 不应该抛出异常


class TestThumbnailGeneration:
    """缩略图生成测试"""

    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def manager_with_pillow(self, temp_dir):
        """创建支持缩略图的管理器"""
        config = ScreenshotConfig(
            local_path=temp_dir,
            thumbnail_size=(160, 360),
            compression="webp",
            quality=60,
        )
        return ScreenshotManager(config)

    def test_generate_thumbnail_inline(self, temp_dir):
        """测试生成缩略图"""
        from PIL import Image

        # 创建一个测试图片
        img_path = Path(temp_dir) / "test.png"
        img = Image.new("RGB", (1080, 2400), color="red")
        img.save(img_path)

        config = ScreenshotConfig(
            local_path=temp_dir,
            thumbnail_size=(160, 360),
        )
        manager = ScreenshotManager(config)

        thumb_path = manager._generate_thumbnail(img_path, 0)

        assert thumb_path is not None
        assert Path(thumb_path).exists()

        # 验证缩略图尺寸
        thumb = Image.open(thumb_path)
        assert thumb.width <= 160
        assert thumb.height <= 360
