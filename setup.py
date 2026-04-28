from setuptools import setup, find_packages

with open("requirements.txt") as f:
    install_requires = [line for line in f.read().strip().split("\n") if line]

setup(
    name="alumicraft",
    version="0.0.1",
    description="Alumicraft custom Frappe app",
    author="Alumicraft",
    author_email="dev@alumicraft.local",
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
    install_requires=install_requires,
)
