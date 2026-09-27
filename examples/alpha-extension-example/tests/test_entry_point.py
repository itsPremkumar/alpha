from importlib.metadata import distribution


def test_installed_distribution_exposes_alpha_extension_entry_point() -> None:
    entry_points = [entry_point for entry_point in distribution("alpha-extension-example").entry_points if entry_point.group == "alpha.extensions"]

    assert [(entry_point.name, entry_point.value) for entry_point in entry_points] == [("example", "alpha_extension_example:install")]

    install = entry_points[0].load()
    assert install.__alpha_api__ == "0.2.0"
    assert install.__alpha_name__ == "example"
