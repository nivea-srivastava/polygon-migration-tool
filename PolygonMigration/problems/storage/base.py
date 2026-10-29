from abc import ABC, abstractmethod


class StorageBackend(ABC):
    """
    Provider-agnostic interface for test-case blob storage.
    All implementations must preserve the canonical key layout:
        test_cases/{db_problem_id}/{nn}
        test_cases/{db_problem_id}/{nn}.a
        test_cases/{db_problem_id}/custom_checker[.exe|.cpp]
    """

    @abstractmethod
    def upload_test_case(self, db_problem_id, test_number, input_data, output_data):
        ...

    @abstractmethod
    def upload_checker(self, db_problem_id, checker_bytes, filename):
        ...

    @abstractmethod
    def empty_problem(self, db_problem_id):
        ...

    @abstractmethod
    def delete_problem(self, db_problem_id):
        ...