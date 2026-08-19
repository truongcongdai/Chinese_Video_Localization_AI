from setuptools import setup, find_packages
from pathlib import Path

this_directory = Path(__file__).parent
requirements = []
req_file = this_directory / "requirements.txt"
if req_file.exists():
    requirements = [r.strip() for r in req_file.read_text(encoding="utf-8").splitlines() if r.strip() and not r.startswith("#") and not r.startswith("--")]

setup(
    name="universal_video_ai",
    version="0.1.0",
    description="Chinese Video Localization AI",
    packages=find_packages("src"),
    package_dir={"": "src"},
    include_package_data=True,
    install_requires=requirements,
    python_requires=">=3.10",
)