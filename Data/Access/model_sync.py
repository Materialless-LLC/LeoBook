# model_sync.py: Upload/download RL model files to/from Supabase Storage.
# Part of LeoBook Data — Access Layer
#
# Classes: ModelSync
# Usage:
#   ModelSync.push()  → uploads Data/Store/models/ → Supabase "models" bucket
#   ModelSync.pull()  → downloads Supabase "models" bucket → Data/Store/models/

import os
import sys
import time
import logging
from pathlib import Path
from typing import Optional, List

from Data.Access.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

# Files to sync (relative to models dir)
MODEL_FILES = [
    "leobook_base.pth",
    "adapter_registry.json",
    "training_config.json",
    "phase1_latest.pth",
    "phase2_latest.pth",
    "phase3_latest.pth",
]

BUCKET_NAME = "models"
PROJECT_ROOT = Path(__file__).parent.parent.parent
MODELS_DIR = PROJECT_ROOT / "Data" / "Store" / "models"

# Files above this size (MB) get a warning and progress indicator
LARGE_FILE_THRESHOLD_MB = 50


def _fmt_size(size_bytes: int) -> str:
    """Format bytes as human-readable string."""
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.0f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def _fmt_elapsed(seconds: float) -> str:
    """Format elapsed time."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    return f"{seconds / 60:.1f}m"


class ModelSync:
    """Upload and download RL model files to/from Supabase Storage."""

    def __init__(self, skip_large: bool = False):
        """
        Args:
            skip_large: If True, skip files > LARGE_FILE_THRESHOLD_MB during push.
        """
        self.supabase = get_supabase_client()
        self.skip_large = skip_large
        if not self.supabase:
            raise RuntimeError("Supabase client not available. Check SUPABASE_URL and SUPABASE_KEY in .env")
        self._ensure_bucket()

    def _ensure_bucket(self):
        """Create the models bucket if it doesn't exist."""
        try:
            buckets = self.supabase.storage.list_buckets()
            exists = any(b.name == BUCKET_NAME for b in buckets)
            if not exists:
                print(f"  [ModelSync] Creating storage bucket: '{BUCKET_NAME}'")
                self.supabase.storage.create_bucket(BUCKET_NAME, options={"public": False})
                print(f"  [ModelSync] ✓ Bucket created")
        except Exception as e:
            logger.warning(f"  [ModelSync] Bucket check/create warning: {e}")

    def _list_local_files(self) -> List[Path]:
        """Find all model files that exist locally."""
        files = []
        for name in MODEL_FILES:
            p = MODELS_DIR / name
            if p.exists():
                files.append(p)

        # Add checkpoint files
        ckpt_dir = MODELS_DIR / "checkpoints"
        if ckpt_dir.exists():
            for p in sorted(ckpt_dir.glob("*.pth")):
                files.append(p)

        return files

    def push(self):
        """Upload all local model files to Supabase Storage."""
        files = self._list_local_files()
        if not files:
            print("  [ModelSync] No model files found in Data/Store/models/. Nothing to push.")
            return

        total_size = sum(f.stat().st_size for f in files)
        print(f"\n  [ModelSync] PUSH: {len(files)} file(s), {_fmt_size(total_size)} total → Supabase Storage")

        # Show manifest
        for f in files:
            sz = f.stat().st_size
            flag = " ⚠ LARGE" if sz > LARGE_FILE_THRESHOLD_MB * 1024 * 1024 else ""
            print(f"    • {f.name} ({_fmt_size(sz)}){flag}")
        print()

        uploaded = 0
        skipped = 0

        for i, local_path in enumerate(files, 1):
            remote_path = str(local_path.relative_to(MODELS_DIR)).replace("\\", "/")
            size_bytes = local_path.stat().st_size
            size_mb = size_bytes / (1024 * 1024)
            is_large = size_mb > LARGE_FILE_THRESHOLD_MB

            # Skip large files if requested
            if self.skip_large and is_large:
                print(f"    [{i}/{len(files)}] ⊘ {remote_path} ({_fmt_size(size_bytes)}) — SKIPPED (--skip-large)")
                skipped += 1
                continue

            # Progress indicator for large files
            if is_large:
                print(f"    [{i}/{len(files)}] ↑ {remote_path} ({_fmt_size(size_bytes)}) — uploading, please wait...", end="", flush=True)
            else:
                print(f"    [{i}/{len(files)}] ↑ {remote_path} ({_fmt_size(size_bytes)})", end="", flush=True)

            try:
                t0 = time.time()
                with open(local_path, "rb") as f:
                    file_content = f.read()

                self.supabase.storage.from_(BUCKET_NAME).upload(
                    path=remote_path,
                    file=file_content,
                    file_options={"x-upsert": "true", "content-type": "application/octet-stream"},
                )
                elapsed = time.time() - t0
                uploaded += 1
                speed = size_mb / elapsed if elapsed > 0 else 0
                print(f" ✓ ({_fmt_elapsed(elapsed)}, {speed:.1f} MB/s)")

                # Free memory immediately after large file upload
                del file_content

            except Exception as e:
                print(f" ✗ FAILED: {e}")

        print(f"\n  [ModelSync] Push complete: {uploaded} uploaded, {skipped} skipped, {len(files) - uploaded - skipped} failed.")

    def pull(self):
        """Download all model files from Supabase Storage."""
        print(f"\n  [ModelSync] PULL: Supabase Storage (bucket: '{BUCKET_NAME}') → Data/Store/models/")

        remote_files = self._list_remote_files()
        if not remote_files:
            print("  [ModelSync] No files found in remote bucket. Nothing to pull.")
            return

        os.makedirs(MODELS_DIR, exist_ok=True)
        os.makedirs(MODELS_DIR / "checkpoints", exist_ok=True)
        downloaded = 0

        print(f"  Found {len(remote_files)} file(s) in remote bucket.\n")

        for i, remote_path in enumerate(remote_files, 1):
            local_path = MODELS_DIR / remote_path.replace("/", os.sep)
            os.makedirs(local_path.parent, exist_ok=True)

            print(f"    [{i}/{len(remote_files)}] ↓ {remote_path}", end="", flush=True)

            try:
                t0 = time.time()
                res = self.supabase.storage.from_(BUCKET_NAME).download(remote_path)
                with open(local_path, "wb") as f:
                    f.write(res)
                elapsed = time.time() - t0
                size_mb = local_path.stat().st_size / (1024 * 1024)
                speed = size_mb / elapsed if elapsed > 0 else 0
                downloaded += 1
                print(f" ✓ ({_fmt_size(local_path.stat().st_size)}, {_fmt_elapsed(elapsed)}, {speed:.1f} MB/s)")
            except Exception as e:
                print(f" ✗ FAILED: {e}")

        print(f"\n  [ModelSync] Pull complete: {downloaded}/{len(remote_files)} files downloaded to {MODELS_DIR}")

    def _list_remote_files(self, prefix: str = "") -> List[str]:
        """Recursively list all files in the remote bucket."""
        files = []
        try:
            items = self.supabase.storage.from_(BUCKET_NAME).list(prefix)
            for item in items:
                name = item.get("name", "")
                if item.get("metadata") is None or item.get("id") is None:
                    sub_prefix = f"{prefix}/{name}" if prefix else name
                    files.extend(self._list_remote_files(sub_prefix))
                else:
                    full_path = f"{prefix}/{name}" if prefix else name
                    files.append(full_path)
        except Exception as e:
            logger.error(f"  [ModelSync] Failed to list remote files: {e}")
        return files
