from app.main import app


def test_application_imports_with_runtime_dependencies():
    assert app.version == "0.6.1"
    assert app.title == "AI SRE RCA"
