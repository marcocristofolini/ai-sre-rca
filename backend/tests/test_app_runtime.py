from app.main import app


def test_application_imports_with_runtime_dependencies():
    assert app.version == "0.8.0"
    assert app.title == "AI SRE RCA"
    paths = app.openapi()["paths"]
    assert "/api/v1/awareness/graph" in paths
    assert "/api/v1/awareness/history/{target_id}" in paths
    assert "/api/v1/awareness/topology" in paths
    assert "/api/v1/awareness/services/{service_name}" in paths
    assert "/api/v1/awareness/blast-radius/{target_id}" in paths
    assert "/api/v1/planner/plan" in paths
    assert "/api/v1/planner/execute" in paths
    assert "/api/v1/planner/investigate" in paths
