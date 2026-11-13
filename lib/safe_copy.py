"""Safe Cross-User File Promotion Utility (SCAR-SYS-05).

Provides safe_copy_file and safe_copy_tree helpers that copy file data
and directory structures without invoking copystat (os.chmod / os.utime),
preventing PermissionError (EPERM) across user boundaries.
"""

import os
import shutil


def safe_copy_file(src: str, dst: str, follow_symlinks: bool = True) -> str:
  """Safely copy a file without copying metadata or calling copystat.

  Uses shutil.copyfile to stream contents directly, preventing EPERM
  errors that occur when shutil.copy2 calls os.chmod or os.utime on
  destinations owned by another user or service account.

  Args:
      src: Path to the source file.
      dst: Path to destination file or directory.
      follow_symlinks: Whether to follow symbolic links in src.

  Returns:
      The path to the destination file.
  """
  if not os.path.isfile(src):
    raise FileNotFoundError(
        f"Source file does not exist or is not a regular file: {src}"
    )

  if os.path.isdir(dst):
    dst = os.path.join(dst, os.path.basename(src))

  parent_dir = os.path.dirname(dst)
  if parent_dir:
    os.makedirs(parent_dir, exist_ok=True)

  shutil.copyfile(src, dst, follow_symlinks=follow_symlinks)
  return dst


def safe_copy_tree(
    src: str,
    dst: str,
    dirs_exist_ok: bool = False,
    follow_symlinks: bool = True,
) -> str:
  """Recursively copy a directory tree using safe_copy_file.

  Creates destination directories with caller permissions (governed by umask)
  and copies all files using shutil.copyfile, completely avoiding copystat.

  Args:
      src: Source directory path.
      dst: Destination directory path.
      dirs_exist_ok: If True, allow destination directory to already exist.
      follow_symlinks: Whether to follow symlinks when copying files.

  Returns:
      The path to the destination directory.
  """
  src_path = os.path.abspath(src)
  dst_path = os.path.abspath(dst)

  if not os.path.isdir(src_path):
    raise NotADirectoryError(
        f"Source directory does not exist or is not a directory: {src}"
    )

  os.makedirs(dst_path, exist_ok=dirs_exist_ok)

  for root, _dirs, files in os.walk(src_path):
    rel_path = os.path.relpath(root, src_path)
    current_dst = os.path.normpath(os.path.join(dst_path, rel_path))
    os.makedirs(current_dst, exist_ok=True)

    for f in files:
      s_file = os.path.join(root, f)
      d_file = os.path.join(current_dst, f)
      safe_copy_file(s_file, d_file, follow_symlinks=follow_symlinks)

  return dst_path
