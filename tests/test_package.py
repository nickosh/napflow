import napflow
import napflow.cli
import napflow.core


def test_package_importable_with_version() -> None:
    assert napflow.__version__
