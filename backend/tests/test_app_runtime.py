from app.main import app


def test_application_imports_with_runtime_dependencies():
    assert app.version == "0.5.0"
    assert app.title == "AI SRE RCA"
