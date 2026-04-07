"""
截图管理器

管理截图的本地存储、缩略图生成和上传
参照 DESIGN.md 中的截图管理设计
"""
import os
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


class ScreenshotMode:
    """截图存储模式"""
    LOCAL = "local"      # 仅本地存储
    UPLOAD = "upload"    # 全部上传
    HYBRID = "hybrid"    # 本地+按需上传


@dataclass
class ScreenshotConfig:
    """截图配置"""
    mode: str = ScreenshotMode.HYBRID
    local_path: str = "./screenshots"
    upload_on: list = field(default_factory=lambda: ["error", "finish", "interrupted"])
    interval: int = 5  # 每 N 步上传一次
    thumbnail_size: tuple[int, int] = (320, 720)
    compression: str = "webp"
    quality: int = 80
    retention_days: int = 7

    def should_upload_on_step(self, step: int, status: str) -> bool:
        """判断是否应该上传"""
        # 按状态上传
        if status in self.upload_on:
            return True
        # 按间隔上传
        if f"interval:{self.interval}" in self.upload_on and step % self.interval == 0:
            return True
        return False


class ScreenshotManager:
    """
    截图管理器

    负责：
    - 保存截图到本地
    - 生成缩略图
    - 按需上传到服务器
    - 清理过期截图
    """

    def __init__(
        self,
        config: Optional[ScreenshotConfig] = None,
        upload_func: Optional[callable] = None,
    ):
        """
        初始化截图管理器

        Args:
            config: 截图配置
            upload_func: 上传函数，签名为 upload_func(path: str) -> str
        """
        self.config = config or ScreenshotConfig()
        self.upload_func = upload_func
        self._executor = ThreadPoolExecutor(max_workers=2)

        # 确保本地目录存在
        Path(self.config.local_path).mkdir(parents=True, exist_ok=True)

    def save(
        self,
        data: bytes,
        task_id: str,
        step: int,
        suffix: str = "png",
    ) -> Optional[str]:
        """
        保存截图

        Args:
            data: 截图数据 (bytes)
            task_id: 任务 ID
            step: 步数
            suffix: 文件后缀

        Returns:
            截图路径，失败返回 None
        """
        import time
        start_time = time.time()

        try:
            # 构建目录和文件名
            task_dir = Path(self.config.local_path) / task_id
            task_dir.mkdir(parents=True, exist_ok=True)

            filename = f"step_{step:04d}.{suffix}"
            filepath = task_dir / filename

            # 保存截图
            with open(filepath, "wb") as f:
                f.write(data)

            elapsed = (time.time() - start_time) * 1000
            logger.debug(f"[save] Screenshot saved: {filepath} ({len(data)} bytes, {elapsed:.1f}ms)",
                       extra={"task_id": task_id, "step": step, "size": len(data), "duration_ms": elapsed})

            # 异步生成缩略图
            self._executor.submit(self._generate_thumbnail, filepath, step)

            return str(filepath)

        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            logger.error(f"[save] Failed to save screenshot for task {task_id} step {step}: {e} ({elapsed:.1f}ms)",
                       extra={"task_id": task_id, "step": step, "error": str(e)})
            return None

    def save_final(
        self,
        data: bytes,
        task_id: str,
        suffix: str = "png",
    ) -> Optional[str]:
        """
        保存最终截图

        Args:
            data: 截图数据
            task_id: 任务 ID
            suffix: 文件后缀

        Returns:
            截图路径
        """
        try:
            task_dir = Path(self.config.local_path) / task_id
            task_dir.mkdir(parents=True, exist_ok=True)

            filename = f"final.{suffix}"
            filepath = task_dir / filename

            with open(filepath, "wb") as f:
                f.write(data)

            return str(filepath)

        except Exception as e:
            logger.error(f"Failed to save final screenshot: {e}")
            return None

    def _generate_thumbnail(self, filepath: Path, step: int) -> Optional[str]:
        """
        生成缩略图

        Args:
            filepath: 原图路径
            step: 步数

        Returns:
            缩略图路径
        """
        try:
            from PIL import Image
            import io

            # 打开原图
            img = Image.open(filepath)

            # 计算缩略图尺寸（保持宽高比）
            target_width, target_height = self.config.thumbnail_size
            img.thumbnail((target_width, target_height), Image.Resampling.LANCZOS)

            # 保存缩略图
            thumb_filename = f"thumbnail_{step:04d}.{self.config.compression}"
            thumb_path = filepath.parent / thumb_filename

            save_kwargs = {"quality": self.config.quality}
            if self.config.compression == "webp":
                img.save(thumb_path, "WEBP", **save_kwargs)
            elif self.config.compression == "jpeg":
                img.save(thumb_path, "JPEG", **save_kwargs)
            else:
                img.save(thumb_path, **save_kwargs)

            logger.debug(f"Thumbnail generated: {thumb_path}")
            return str(thumb_path)

        except ImportError:
            logger.warning("Pillow not installed, thumbnail generation skipped")
            return None
        except Exception as e:
            logger.error(f"Failed to generate thumbnail: {e}")
            return None

    def upload(
        self,
        filepath: str,
        task_id: str,
        step: int,
    ) -> Optional[str]:
        """
        上传截图

        Args:
            filepath: 本地路径
            task_id: 任务 ID
            step: 步数

        Returns:
            上传后的 URL，失败返回 None
        """
        import time
        start_time = time.time()

        if not self.upload_func:
            logger.debug("[upload] No upload function configured")
            return None

        try:
            logger.debug(f"[upload] Uploading screenshot: {filepath}", extra={"task_id": task_id, "step": step})
            url = self.upload_func(filepath)
            elapsed = (time.time() - start_time) * 1000
            logger.info(f"[upload] Screenshot uploaded: {url} ({elapsed:.1f}ms)",
                       extra={"task_id": task_id, "step": step, "url": url, "duration_ms": elapsed})
            return url
        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            logger.error(f"[upload] Failed to upload screenshot {filepath}: {e} ({elapsed:.1f}ms)",
                        extra={"task_id": task_id, "step": step, "filepath": filepath, "error": str(e)})
            return None

    def cleanup_old(self, days: Optional[int] = None) -> int:
        """
        清理过期截图

        Args:
            days: 保留天数，默认使用配置

        Returns:
            清理的文件数
        """
        days = days or self.config.retention_days
        cutoff = datetime.now().timestamp() - days * 86400
        cleaned = 0

        try:
            base_path = Path(self.config.local_path)
            if not base_path.exists():
                return 0

            for task_dir in base_path.iterdir():
                if not task_dir.is_dir():
                    continue

                for filepath in task_dir.iterdir():
                    if filepath.stat().st_mtime < cutoff:
                        filepath.unlink()
                        cleaned += 1

            logger.info(f"Cleaned {cleaned} old screenshots")

        except Exception as e:
            logger.error(f"Failed to cleanup screenshots: {e}")

        return cleaned

    def get_screenshot_urls(
        self,
        task_id: str,
        start_step: int = 0,
        end_step: Optional[int] = None,
    ) -> list[dict]:
        """
        获取任务的所有截图信息

        Args:
            task_id: 任务 ID
            start_step: 起始步数
            end_step: 结束步数

        Returns:
            截图信息列表
        """
        screenshots = []

        try:
            task_dir = Path(self.config.local_path) / task_id
            if not task_dir.exists():
                return screenshots

            for filepath in sorted(task_dir.iterdir()):
                if filepath.name.startswith("step_") and filepath.suffix == ".png":
                    # 提取步数
                    step_str = filepath.stem.replace("step_", "")
                    try:
                        step = int(step_str)
                    except ValueError:
                        continue

                    if step < start_step:
                        continue
                    if end_step and step > end_step:
                        continue

                    screenshots.append({
                        "step": step,
                        "path": str(filepath),
                        "url": None,  # 未上传
                    })

        except Exception as e:
            logger.error(f"Failed to get screenshot URLs: {e}")

        return screenshots

    def get_storage_size(self) -> int:
        """
        获取本地存储大小（字节）

        Returns:
            存储大小
        """
        total = 0
        try:
            base_path = Path(self.config.local_path)
            if base_path.exists():
                for filepath in base_path.rglob("*"):
                    if filepath.is_file():
                        total += filepath.stat().st_size
        except Exception as e:
            logger.error(f"Failed to calculate storage size: {e}")

        return total

    def close(self) -> None:
        """关闭管理器"""
        self._executor.shutdown(wait=True)
