from setuptools import setup, find_packages

setup(
    name="sdkingest",
    version="0.1.0",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    install_requires=[
        "click>=8.0.0",
        "fastapi[standard]",
        "python-dotenv",
        "slowapi",
        "starlette",
        "tiktoken",
        "tomli",
        "uvicorn",
    ],
) 