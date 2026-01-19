from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from app.database import get_session
from app.main import app
from app.models import TestFailure, TestRun


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture(name="client")
def client_fixture(session: Session):
    def get_session_override():
        return session

    app.dependency_overrides[get_session] = get_session_override
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "reachable"}


def test_upload_test_results_success(client):
    # Example minimal valid XML with one failure
    xml_content = b"""<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
  <testsuite name="suite">
    <testcase classname="classA" name="test1">
      <failure>Failure message here</failure>
    </testcase>
  </testsuite>
</testsuites>"""

    response = client.post(
        "/upload-test-results/",
        data={
            "branch": "main",
            "revision": "rev123",
            "platform": "linux",
            "bbnum": "42",
            "typ": "test",
        },
        files={"file": ("test-results.xml", xml_content, "application/xml")},
    )
    assert response.status_code == 200
    assert "Results were stored successfully" in response.json()["detail"]


def test_upload_test_results_bad_extension(client):
    response = client.post(
        "/upload-test-results/",
        data={
            "branch": "main",
            "revision": "rev123",
            "platform": "linux",
            "bbnum": "42",
            "typ": "test",
        },
        files={"file": ("badfile.txt", b"some text", "application/json")},
    )
    assert response.status_code == 400
    assert "Unsupported file content type" in response.json()["detail"]


def test_upload_real_test_results(client, session):
    xml_path = Path("tests/integration/sample/mtr_01.xml")
    with open(xml_path, "rb") as f:
        file_bytes = f.read()

    response = client.post(
        "/upload-test-results/",
        data={
            "branch": "main",
            "revision": "rev123",
            "platform": "linux",
            "bbnum": "42",
            "typ": "test",
        },
        files={"file": ("real_test_results.xml", file_bytes, "application/xml")},
    )

    assert response.status_code == 200
    assert "Results were stored successfully" in response.json()["detail"]

    # Verify DB entries
    # Example: Verify a test run was created
    test_runs = session.exec(select(TestRun)).all()
    assert len(test_runs) == 1
    assert test_runs[0].branch == "main"

    # Verify test cases (adjust to your actual schema logic)
    test_cases = session.exec(select(TestFailure)).all()
    assert any(tc.test_name == "archive.archive" for tc in test_cases)

def test_upload_real_test_results_log(client, session):
    log_path = Path("tests/integration/sample/rocksdb.stdout")
    with open(log_path, "rb") as f:
        file_bytes = f.read()

    response = client.post(
        "/upload-test-results/",
        data={
            "branch": "main",
            "revision": "rev123",
            "platform": "linux",
            "bbnum": "42",
            "typ": "test",
        },
        files={"file": ("real_test_results.log", file_bytes, "text/plain")},
    )

    assert response.status_code == 200
    assert "Results were stored successfully" in response.json()["detail"]

    # Verify DB entries
    # Example: Verify a test run was created
    test_runs = session.exec(select(TestRun)).all()
    assert len(test_runs) == 1
    assert test_runs[0].branch == "main"

    # Verify test cases (adjust to your actual schema logic)
    test_cases = session.exec(select(TestFailure)).all()
    assert any(tc.test_name == "rocksdb.innodb_i_s_tables_disabled" for tc in test_cases)

def test_nothing_to_upload(client, session):
    xml_path = Path("tests/integration/sample/mtr_02.xml")
    with open(xml_path, "rb") as f:
        file_bytes = f.read()

    response = client.post(
        "/upload-test-results/",
        data={
            "branch": "main",
            "revision": "rev123",
            "platform": "linux",
            "bbnum": "42",
            "typ": "test",
        },
        files={"file": ("real_test_results.xml", file_bytes, "application/xml")},
    )

    assert response.status_code == 200
    assert "No test failures to store" in response.json()["detail"]

def test_nothing_to_upload_log(client, session):
    log_path = Path("tests/integration/sample/nothing.stdout")
    with open(log_path, "rb") as f:
        file_bytes = f.read()

    response = client.post(
        "/upload-test-results/",
        data={
            "branch": "main",
            "revision": "rev123",
            "platform": "linux",
            "bbnum": "42",
            "typ": "test",
        },
        files={"file": ("real_test_results.log", file_bytes, "text/plain")},
    )

    assert response.status_code == 200
    assert "No test failures to store" in response.json()["detail"]


def test_upload_real_test_results_galera(client):
    xml_path = Path("tests/integration/sample/galera.xml")
    with open(xml_path, "rb") as f:
        file_bytes = f.read()

    response = client.post(
        "/upload-test-results/",
        data={
            "branch": "main",
            "revision": "470bfcabfd0c0bb0a609fc6211af7d07cb4c0710",
            "platform": "amd64-debian-12-deb-autobake-migration",
            "bbnum": "16",
            "typ": "galera",
        },
        files={"file": ("real_test_results.xml", file_bytes, "application/xml")},
    )

    assert response.status_code == 200
    assert "Results were stored successfully" in response.json()["detail"]

def test_upload_real_test_results_rocksdb(client):
    xml_path = Path("tests/integration/sample/rocksdb.xml")
    with open(xml_path, "rb") as f:
        file_bytes = f.read()

    response = client.post(
        "/upload-test-results/",
        data={
            "branch": "main",
            "revision": "470bfcabfd0c0bb0a609fc6211af7d07cb4c0710",
            "platform": "amd64-debian-12-deb-autobake-migration",
            "bbnum": "16",
            "typ": "rocksdb",
        },
        files={"file": ("real_test_results.xml", file_bytes, "application/xml")},
    )

    print(response.json())
    assert response.status_code == 200
    assert "Results were stored successfully" in response.json()["detail"]

def test_upload_real_test_results_normal(client):
    xml_path = Path("tests/integration/sample/nm.xml")
    with open(xml_path, "rb") as f:
        file_bytes = f.read()

    response = client.post(
        "/upload-test-results/",
        data={
            "branch": "main",
            "revision": "470bfcabfd0c0bb0a609fc6211af7d07cb4c0710",
            "platform": "amd64-debian-12-deb-autobake-migration",
            "bbnum": "16",
            "typ": "rocksdb",
        },
        files={"file": ("real_test_results.xml", file_bytes, "application/xml")},
    )

    print(response.json())
    assert response.status_code == 200
    assert "No test failures to store" in response.json()["detail"]

def test_upload_real_test_results_s3(client):
    xml_path = Path("tests/integration/sample/s3.xml")
    with open(xml_path, "rb") as f:
        file_bytes = f.read()

    response = client.post(
        "/upload-test-results/",
        data={
            "branch": "main",
            "revision": "470bfcabfd0c0bb0a609fc6211af7d07cb4c0710",
            "platform": "amd64-debian-12-deb-autobake-migration",
            "bbnum": "16",
            "typ": "rocksdb",
        },
        files={"file": ("real_test_results.xml", file_bytes, "application/xml")},
    )

    print(response.json())
    assert response.status_code == 200
    assert "No test failures to store" in response.json()["detail"]