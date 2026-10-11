from setuptools import setup, find_packages

setup(
    name="vibeguard",
    version="1.0.0",
    description="The AI Vibe Coding Invariant & Drift Engine",
    author="Antigravity AI",
    packages=find_packages(),
    package_data={
        "vibeguard": ["ui_assets/*"],
    },
    entry_points={
        "console_scripts": [
            "vibeguard = vibeguard.cli:main",
        ],
    },
    python_requires=">=3.8",
)
