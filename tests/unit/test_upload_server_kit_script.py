from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UPLOAD_SCRIPT = REPO_ROOT / "scripts" / "upload_server_kit.ps1"
UPLOAD_GUIDE = REPO_ROOT / "docs" / "setup" / "服务器离线测试指南.md"


def test_upload_script_supports_first_upload_and_resume() -> None:
    script = UPLOAD_SCRIPT.read_text(encoding="utf-8")

    assert 'Get-ChildItem -LiteralPath $KitRoot -Directory -Recurse' in script
    assert 'if [ ! -e "$destination" ]; then : > "$destination"; fi' in script
    assert '$uploadBatch.Add("put -a ' in script
    assert 'put -R' not in script
    assert 'GetRelativePath' not in script
    assert "sha256sum -c SHA256SUMS" in script


def test_upload_guide_matches_the_implemented_transport() -> None:
    guide = UPLOAD_GUIDE.read_text(encoding="utf-8")

    assert "逐文件使用 `sftp put -a`" in guide
    assert "递归 `put -R`" in guide
    assert "零字节占位符" in guide
