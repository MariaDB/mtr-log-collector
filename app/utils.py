import logging
import re
from typing import List, Set, Tuple

from fastapi import HTTPException, status
from junitparser import JUnitXml
from sqlmodel import Session

from app.models import TestFailure, TestRun

logger = logging.getLogger(__name__)


from abc import ABC, abstractmethod


class TestFailureCollection(ABC):
    def __init__(self):
        self._failures = []
        self.data = None

    @abstractmethod
    def parse(self, content: str):
        pass

    @abstractmethod
    def extract_failures(self, test_run_id: int) -> List[TestFailure]:
        pass

    @property
    def failures(self) -> List[TestFailure]:
        return self._failures


class XMLTestFailureCollection(TestFailureCollection):
    def parse(self, content: str):
        try:
            self.data = JUnitXml.fromstring(content)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JUnit XML format",
            )

    def extract_failures(self, test_run_id: int):
        seen: Set[Tuple[int, str, str]] = (
            set()
        )  # To track (test_run_id, test_name, test_variant)

        for suite in self.data:
            for case in suite:
                if case.is_failure:
                    for result in case:
                        test_suite = case.classname
                        test_name = case.name
                        test_variant = case._elem.attrib.get("combinations", "N/A")
                        key = (test_run_id, test_name, test_variant)

                        if key not in seen:
                            seen.add(key)
                            self._failures.append(
                                TestFailure(
                                    test_run_id=test_run_id,
                                    test_name=test_suite + "." + test_name,
                                    test_variant=test_variant,
                                    failure_text=result.text,
                                )
                            )


class LogTestFailureCollection(TestFailureCollection):
    FAILURE_BLOCK_START = re.compile(
        r"^([-._/0-9a-zA-z]+)( '[-_ ,0-9a-zA-Z]+')?\s+(w[0-9]+\s+)?\[ (fail|pass) \]\s*(.*)$"
    )
    WARNING_BLOCK_START = re.compile(
        r"^\*\*\*Warnings generated in error logs during shutdown after running tests: (.*)"
    )
    FAILURE_BLOCK_END_1 = re.compile(
        r"^[-._/0-9a-zA-z]+( '[-_ ,0-9a-zA-Z]+')?\s+(w[0-9]+\s+)?\[ [-a-z]+ \]"
    )
    FAILURE_BLOCK_END_2 = re.compile(r"^The servers were restarted [0-9]+ times$")
    FAILURE_BLOCK_END_3 = re.compile(r"^Only\s+[0-9]+\s+of\s+[0-9]+\s+completed.$")
    TERMINATOR_RES = (FAILURE_BLOCK_END_1, FAILURE_BLOCK_END_2, FAILURE_BLOCK_END_3)
    TIMEOUT_LINE = "Test suite timeout! Terminating..."
    INCOMPLETE_PREFIX = "mysql-test-run: *** ERROR: Not all tests completed"
    DASHES = "-" * 60

    def parse(self, content: str):
        self.data = content.splitlines()

    def extract_failures(self, test_run_id):
        def normalize_variant(raw: str | None) -> str:
            return "" if raw is None else raw[2:-1]

        def is_failure_block_terminator(line: str) -> bool:
            return (
                line == self.TIMEOUT_LINE
                or line.startswith(self.INCOMPLETE_PREFIX)
                or line.startswith(self.DASHES)
                or any(rx.search(line) for rx in self.TERMINATOR_RES)
            )

        def commit_current() -> None:
            nonlocal current, current_buf
            if current is None:
                return
            current.failure_text = "".join(current_buf)
            self.failures.append(current)
            current = None
            current_buf = []

        current: TestFailure | None = None
        current_buf: list[str] = (
            []
        )  # avoids O(n^2) string concatenation for large blocks

        for raw_line in self.data:
            line = raw_line.rstrip("\r\n")

            # 1) Test result line: starts a new context; ends any previous failure block.
            m = self.FAILURE_BLOCK_START.search(line)
            if m:
                testname, variant, worker, result, info = m.groups()
                commit_current()

                if result == "fail":
                    current = TestFailure(
                        test_run_id=test_run_id,
                        test_name=testname,
                        test_variant=normalize_variant(variant),
                        info_text=info,
                        failure_text="",  # filled on commit
                    )
                    current_buf = [line + "\n"]
                continue

            # 2) Warning line: ends any previous failure block; warnings aren't captured.
            if self.WARNING_BLOCK_START.search(line):
                commit_current()
                continue

            # 3) Known terminators: we're done with the current failure block.
            if is_failure_block_terminator(line):
                commit_current()
                continue

            # 4) Otherwise, if we're in a failure block, keep accumulating.
            if current is not None:
                current_buf.append(line + "\n")

        commit_current()


def insert_test_run(session: Session, **kwargs) -> TestRun:
    test_run = TestRun(**kwargs)
    try:
        session.add(test_run)
        session.commit()
        session.refresh(test_run)
    except Exception as e:
        logger.error(f"Failed to insert test run: {str(e)}")
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to insert test run: {str(e)}",
        )
    return test_run
