import os
from typing import Any

from core.services import podcast_naming as pn


class CacheService:
    def __init__(self, storage: Any, cache_dir: str, podcast_dir: str) -> None:
        self.storage = storage
        self.cache_dir = cache_dir
        self.podcast_dir = podcast_dir

    def list_items(self) -> list[dict[str, Any]]:
        items = self.storage.get_all_cache()
        for item in items:
            md5 = item.get("md5")
            # M4-⑤:成品文件名文法归 podcast_naming,这里只问不猜
            is_exported = False
            if md5 and os.path.exists(self.podcast_dir):
                for filename in os.listdir(self.podcast_dir):
                    if pn.is_output_for_md5(filename, md5):
                        is_exported = True
                        break
            item["is_exported"] = is_exported
        return items

    def get_text(self, md5: str | None) -> str | None:
        item = self.storage.get_cache_by_md5(md5)
        if not item:
            return None
        return item.get("text", "")

    def delete(self, md5: str | None) -> None:
        self.storage.delete_cache_by_md5(md5)

    def clear(self) -> None:
        try:
            for filename in os.listdir(self.cache_dir):
                os.remove(os.path.join(self.cache_dir, filename))
        except Exception:
            pass

        # 通过 Storage 抽象删除元数据（WAL/timeout 一致），不再绕过开裸连接、不再静默吞错。
        self.storage.clear_cache()
