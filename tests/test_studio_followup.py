import shutil
import subprocess
from pathlib import Path
import pytest
from app import config


@pytest.mark.skipif(not shutil.which('node'), reason='Node.js unavailable')
def test_studio_browser_logic():
    subprocess.run([shutil.which('node'), str(Path(__file__).with_suffix('.cjs'))],
                   cwd=config.ROOT, check=True, timeout=30)
